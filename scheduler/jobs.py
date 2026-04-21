import asyncio
import datetime

import structlog
from sqlalchemy import or_, select
from sqlalchemy.exc import SQLAlchemyError

from collector.article_extractor import extract_article_content
from collector.dedup import deduplicate_articles
from collector.rss_fetcher import compute_content_hash, fetch_all_feeds
from config.time_utils import local_today, utc_now
from db.engine import async_session
from db.models import NewsArticle, PipelineRun, Subscriber
from delivery.whatsapp_sender import send_digest
from processor.summarizer import generate_all_summaries

logger = structlog.get_logger()

_pipeline_lock = asyncio.Lock()

# Timeout constants (in seconds)
TIMEOUT_FETCH_FEEDS = 5 * 60  # 5 minutes for fetching all feeds
TIMEOUT_EXTRACT_ARTICLE = 120  # 2 minutes per article extraction
TIMEOUT_SUMMARIZE = 3 * 60  # Minimum total budget for summary generation
TIMEOUT_SUMMARIZE_PER_CATEGORY = 75
MAX_CONCURRENT_EXTRACTIONS = 5


async def run_pipeline(period: str) -> None:
    """Run the full news collection, processing, and delivery pipeline."""
    async with _pipeline_lock:
        await _run_pipeline_impl(period)


async def _run_pipeline_impl(period: str) -> None:
    """Internal implementation of the pipeline (always called within the lock).
    
    Features:
    - Timeouts on each step to prevent deadlocks
    - Specific exception handling for better debugging
    - Detailed error logging indicating which step failed
    """
    run = await _create_pipeline_run(period)

    try:
        # STEP 1: Collect news with timeout
        logger.info(f"Starting {period} pipeline - collecting news (timeout: {TIMEOUT_FETCH_FEEDS}s)")
        try:
            entries = await asyncio.wait_for(
                fetch_all_feeds(hours=12),
                timeout=TIMEOUT_FETCH_FEEDS
            )
        except asyncio.TimeoutError:
            logger.error(f"STEP 1 TIMEOUT: fetch_all_feeds took longer than {TIMEOUT_FETCH_FEEDS}s")
            await _update_pipeline_run(
                run.id, "failed",
                error_log=f"Timeout in feed collection (>{TIMEOUT_FETCH_FEEDS}s)"
            )
            await _alert_admin(f"Pipeline {period}: STEP 1 TIMEOUT (feed collection)")
            return
        except Exception as e:
            logger.error(
                f"STEP 1 ERROR: Failed to collect feeds: "
                f"{type(e).__name__}: {e}"
            )
            await _update_pipeline_run(
                run.id, "failed",
                error_log=f"Feed collection error: {type(e).__name__}"
            )
            return

        if not entries:
            logger.warning(f"No articles collected in {period} pipeline")
            await _update_pipeline_run(run.id, "completed", articles_collected=0)
            return

        # STEP 2: Deduplicate
        try:
            new_entries = await deduplicate_articles(entries)
            logger.info(f"Collected {len(entries)} articles, {len(new_entries)} are new")
        except Exception as e:
            logger.error(
                f"STEP 2 ERROR: Deduplication failed: "
                f"{type(e).__name__}: {e}"
            )
            await _update_pipeline_run(run.id, "failed", error_log=f"Dedup error: {type(e).__name__}")
            return

        # STEP 3: Extract full content and save articles with per-article timeout
        extraction_semaphore = asyncio.Semaphore(MAX_CONCURRENT_EXTRACTIONS)
        extraction_results = await asyncio.gather(
            *[
                _extract_and_save_entry(entry, idx, len(new_entries), extraction_semaphore)
                for idx, entry in enumerate(new_entries, 1)
            ]
        )
        articles = [article for article, had_error in extraction_results if article]
        extraction_errors = sum(1 for article, had_error in extraction_results if had_error)

        if extraction_errors > 0:
            logger.warning(
                f"STEP 3: Extracted {len(articles)} articles with "
                f"{extraction_errors} errors/timeouts"
            )
        else:
            logger.info(f"Saved {len(articles)} articles with content")
            
        await _update_pipeline_run(run.id, "running", articles_collected=len(articles))

        if not articles:
            logger.warning("No articles with content to process")
            await _update_pipeline_run(run.id, "completed")
            return

        # STEP 4: Generate summaries with LLM with timeout
        categories_present = {article.category for article in articles}
        summarize_timeout = max(
            TIMEOUT_SUMMARIZE,
            len(categories_present) * TIMEOUT_SUMMARIZE_PER_CATEGORY,
        )
        logger.info(
            f"Generating summaries for {len(articles)} articles "
            f"(timeout: {summarize_timeout}s)"
        )
        try:
            summaries = await asyncio.wait_for(
                generate_all_summaries(articles, period),
                timeout=summarize_timeout
            )
            logger.info(f"Generated {len(summaries)} summaries")
        except asyncio.TimeoutError:
            logger.error(
                f"STEP 4 TIMEOUT: LLM summarization took longer than {summarize_timeout}s"
            )
            await _update_pipeline_run(
                run.id, "failed",
                error_log=f"Timeout in summarization (>{summarize_timeout}s)"
            )
            await _alert_admin(f"Pipeline {period}: STEP 4 TIMEOUT (summarization)")
            return
        except ValueError as e:
            # LLM returned invalid JSON after retries
            logger.error(f"STEP 4 ERROR: LLM JSON parsing failed: {e}")
            await _update_pipeline_run(run.id, "failed", error_log="LLM JSON parsing error")
            return
        except Exception as e:
            logger.error(
                f"STEP 4 ERROR: Summarization failed: "
                f"{type(e).__name__}: {e}"
            )
            await _update_pipeline_run(run.id, "failed", error_log=f"Summarization error: {type(e).__name__}")
            return

        await _update_pipeline_run(run.id, "running", summaries_generated=len(summaries))

        if not summaries:
            logger.warning("No summaries generated")
            await _update_pipeline_run(run.id, "completed")
            return

        # STEP 5: Deliver to subscribers
        try:
            async with async_session() as session:
                result = await session.execute(
                    select(Subscriber).where(Subscriber.active == True)  # noqa: E712
                )
                subscribers = list(result.scalars().all())
        except SQLAlchemyError as e:
            logger.error(f"STEP 5 ERROR: Failed to fetch subscribers: {e}")
            await _update_pipeline_run(run.id, "failed", error_log="DB error fetching subscribers")
            return

        if not subscribers:
            logger.info("No active subscribers to deliver to")
            await _update_pipeline_run(run.id, "completed")
            return

        try:
            sent_count = await send_digest(subscribers, summaries, period)
            logger.info(f"Sent {sent_count} messages to {len(subscribers)} subscribers")
            await _update_pipeline_run(run.id, "completed", messages_sent=sent_count)
        except Exception as e:
            logger.error(
                f"STEP 5 ERROR: Delivery failed: {type(e).__name__}: {e}"
            )
            await _update_pipeline_run(run.id, "failed", error_log=f"Delivery error: {type(e).__name__}")
            await _alert_admin(f"Pipeline {period}: STEP 5 ERROR (delivery failed)")

    except asyncio.CancelledError:
        logger.warning(f"{period} pipeline was cancelled")
        await _update_pipeline_run(run.id, "failed", error_log="Pipeline cancelled")
    except Exception as e:
        # Catch-all for unexpected errors
        logger.exception(f"{period} pipeline failed with unexpected error")
        await _update_pipeline_run(run.id, "failed", error_log=f"Unexpected: {type(e).__name__}")
        await _alert_admin(f"Pipeline {period} unexpected error: {type(e).__name__}: {e}")

    finally:
        await _finish_pipeline_run(run.id)


async def run_morning_pipeline() -> None:
    await run_pipeline("morning")


async def run_midday_pipeline() -> None:
    await run_pipeline("midday")


async def run_afternoon_pipeline() -> None:
    await run_pipeline("afternoon")


async def run_evening_pipeline() -> None:
    await run_pipeline("evening")


async def cleanup_old_articles(days: int = 7) -> None:
    """Remove articles older than the specified number of days."""
    from sqlalchemy import delete as sql_delete

    cutoff = utc_now() - datetime.timedelta(days=days)

    async with async_session() as session:
        result = await session.execute(
            sql_delete(NewsArticle).where(NewsArticle.fetched_at < cutoff)
        )
        await session.commit()

    logger.info(f"Cleaned up {result.rowcount} articles older than {days} days")


async def check_feed_health() -> None:
    """Check feed health. Already handled in rss_fetcher.py via consecutive_errors.
    This job can be used for additional health checks or re-enabling feeds.
    """
    async with async_session() as session:
        from db.models import FeedSource

        result = await session.execute(
            select(FeedSource).where(FeedSource.active == False)  # noqa: E712
        )
        inactive_feeds = result.scalars().all()

    if inactive_feeds:
        feed_names = ", ".join(f.name for f in inactive_feeds)
        await _alert_admin(f"Feeds inativos: {feed_names}")


# --- Helper functions ---


async def _create_pipeline_run(period: str) -> PipelineRun:
    async with async_session() as session:
        run = PipelineRun(
            period=period,
            date=local_today(),
            status="running",
            started_at=utc_now(),
        )
        session.add(run)
        await session.commit()
        await session.refresh(run)
        return run


async def _update_pipeline_run(
    run_id: int,
    status: str,
    articles_collected: int | None = None,
    summaries_generated: int | None = None,
    messages_sent: int | None = None,
    error_log: str | None = None,
) -> None:
    async with async_session() as session:
        run = await session.get(PipelineRun, run_id)
        if not run:
            return

        run.status = status
        if articles_collected is not None:
            run.articles_collected = articles_collected
        if summaries_generated is not None:
            run.summaries_generated = summaries_generated
        if messages_sent is not None:
            run.messages_sent = messages_sent
        if error_log is not None:
            run.error_log = error_log

        await session.commit()


async def _finish_pipeline_run(run_id: int) -> None:
    async with async_session() as session:
        run = await session.get(PipelineRun, run_id)
        if run:
            run.finished_at = utc_now()
            await session.commit()


async def _save_article(
    source_id: int,
    url: str,
    title: str,
    raw_content: str,
    category: str,
    published_at: datetime.datetime,
    content_hash: str,
) -> NewsArticle | None:
    """Save a new article to the database. Returns None if URL/hash already exists."""
    async with async_session() as session:
        result = await session.execute(
            select(NewsArticle).where(
                or_(
                    NewsArticle.url == url,
                    NewsArticle.content_hash == content_hash,
                )
            )
        )
        if result.scalar_one_or_none():
            return None

        article = NewsArticle(
            source_id=source_id,
            url=url,
            title=title,
            raw_content=raw_content,
            category=category,
            published_at=published_at,
            content_hash=content_hash,
        )
        session.add(article)
        try:
            await session.commit()
            await session.refresh(article)
        except Exception as e:
            await session.rollback()
            logger.exception(f"Failed to save article: {type(e).__name__}: {e}")
            return None

        # Eagerly load source relationship
        result = await session.execute(
            select(NewsArticle).where(NewsArticle.id == article.id)
        )
        return result.scalar_one_or_none()


async def _extract_and_save_entry(
    entry: dict,
    idx: int,
    total: int,
    semaphore: asyncio.Semaphore,
) -> tuple[NewsArticle | None, bool]:
    async with semaphore:
        try:
            logger.debug(f"Extracting content for article {idx}/{total}")
            content = await asyncio.wait_for(
                extract_article_content(
                    entry["url"], entry.get("description", "")
                ),
                timeout=TIMEOUT_EXTRACT_ARTICLE,
            )
            if not content:
                logger.debug(f"No content extracted for {entry['url']}")
                return None, False

            content_hash = compute_content_hash(content)
            article = await _save_article(
                source_id=entry["source_id"],
                url=entry["url"],
                title=entry["title"],
                raw_content=content,
                category=entry["category"],
                published_at=entry["published_at"],
                content_hash=content_hash,
            )
            return article, False
        except asyncio.TimeoutError:
            logger.warning(
                f"Article {idx} extraction timeout (>{TIMEOUT_EXTRACT_ARTICLE}s): "
                f"{entry.get('url', 'unknown')}"
            )
            return None, True
        except Exception as e:
            logger.warning(
                f"Article {idx} extraction error: {type(e).__name__}: {e}"
            )
            return None, True


async def _alert_admin(message: str) -> None:
    """Send an alert to the admin phone number."""
    from config.settings import settings

    if not settings.admin_phone:
        logger.warning(f"No admin phone configured, would alert: {message}")
        return

    from delivery.whatsapp_sender import send_single_message

    await send_single_message(settings.admin_phone, f"ALERTA NewsBot: {message}")
