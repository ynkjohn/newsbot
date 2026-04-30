"""Scheduled jobs — pipeline orchestration and maintenance tasks.

The pipeline is decomposed into discrete steps (``_step_*`` functions),
each returning a ``StepResult``.  The orchestrator (``_run_pipeline_impl``)
chains them through ``execute_step`` which handles event recording,
timeouts, and error classification.
"""

import asyncio
import datetime

import structlog
from sqlalchemy import or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import selectinload

from collector.article_extractor import extract_article_content
from collector.dedup import deduplicate_articles
from collector.rss_fetcher import compute_content_hash, fetch_all_feeds
from config.time_utils import utc_now
from db.engine import async_session
from db.models import VALID_CATEGORIES, NewsArticle, Subscriber
from delivery.whatsapp_sender import send_digest
from processor.summarizer import generate_all_summaries
from scheduler.step_runner import (
    StepResult,
    alert_admin,
    create_pipeline_run,
    execute_step,
    finish_pipeline_run,
    record_pipeline_event,
    update_pipeline_run,
)

logger = structlog.get_logger()

_pipeline_lock = asyncio.Lock()

# Timeout constants (in seconds)
TIMEOUT_FETCH_FEEDS = 5 * 60  # 5 minutes for fetching all feeds
TIMEOUT_EXTRACT_ARTICLE = 120  # 2 minutes per article extraction
TIMEOUT_SUMMARIZE = 3 * 60  # Minimum total budget for summary generation
TIMEOUT_SUMMARIZE_PER_CATEGORY = 75
MAX_CONCURRENT_EXTRACTIONS = 5


# ---------------------------------------------------------------------------
# Pipeline step functions
# ---------------------------------------------------------------------------


async def _step_fetch_feeds() -> StepResult:
    """Step 1: Collect news entries from all active RSS feeds."""
    entries = await fetch_all_feeds(hours=12)
    return StepResult(
        status="ok",
        message="Coleta de feeds finalizada",
        metadata={"entries": len(entries)},
        payload=entries,
    )


async def _step_deduplicate(entries: list[dict]) -> StepResult:
    """Step 2: Remove entries already seen in the database."""
    new_entries = await deduplicate_articles(entries)
    logger.info(f"Collected {len(entries)} articles, {len(new_entries)} are new")
    return StepResult(
        status="ok",
        message="Deduplicação finalizada",
        metadata={"entries": len(entries), "newEntries": len(new_entries)},
        payload=new_entries,
    )


async def _step_extract_and_save_all(new_entries: list[dict]) -> StepResult:
    """Step 3: Extract full content for each entry and save to DB."""
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_EXTRACTIONS)
    extraction_results = await asyncio.gather(
        *[
            _extract_and_save_entry(entry, idx, len(new_entries), semaphore)
            for idx, entry in enumerate(new_entries, 1)
        ]
    )
    articles = [article for article, had_error in extraction_results if article]
    errors = sum(1 for _, had_error in extraction_results if had_error)

    if errors > 0:
        logger.warning(f"Extracted {len(articles)} articles with {errors} errors/timeouts")
    else:
        logger.info(f"Saved {len(articles)} articles with content")

    return StepResult(
        status="ok",
        message="Extração de conteúdo finalizada",
        metadata={"articles": len(articles), "errors": errors},
        payload=articles,
    )


async def _step_summarize(
    articles: list[NewsArticle],
    period: str,
    *,
    replace_existing_summaries: bool = False,
) -> StepResult:
    """Step 4: Generate LLM summaries for the collected articles.
    
    Scheduled runs also fetch unprocessed articles left by failed runs. Manual
    replacement runs fetch the day's stored articles so summaries are refreshed
    from the full available context, not only from newly collected rows.
    """
    from config.time_utils import local_today
    
    today = local_today()
    start_of_day = datetime.datetime.combine(
        today,
        datetime.time.min,
        tzinfo=datetime.timezone.utc,
    )
    article_filters = [
        NewsArticle.published_at >= start_of_day,
        NewsArticle.raw_content.is_not(None),
        NewsArticle.raw_content != "",
    ]
    if not replace_existing_summaries:
        article_filters.append(NewsArticle.processed.is_(False))
    
    async with async_session() as session:
        result = await session.execute(
            select(NewsArticle)
            .options(selectinload(NewsArticle.source))
            .where(*article_filters)
        )
        db_articles = result.scalars().all()
        
    # Combine the new articles with any unprocessed ones from the DB
    # We use a dict to deduplicate by ID in case they overlap
    all_articles_dict = {a.id: a for a in articles if a.id}
    for a in db_articles:
        if a.id:
            all_articles_dict[a.id] = a
            
    combined_articles = list(all_articles_dict.values())
    combined_article_ids = [article.id for article in combined_articles if article.id]
    if combined_article_ids:
        async with async_session() as session:
            result = await session.execute(
                select(NewsArticle)
                .options(selectinload(NewsArticle.source))
                .where(NewsArticle.id.in_(combined_article_ids))
            )
            combined_articles = list(result.scalars().all())

    if not combined_articles:
        combined_articles = articles # fallback to just the new ones if db fetch was weird
        
    summaries = await generate_all_summaries(
        combined_articles,
        period,
        replace_existing=replace_existing_summaries,
    )
    
    # Now fetch ALL summaries for this period today to ensure complete delivery
    # (combining any previously generated summaries that were skipped with the new ones)
    async with async_session() as session:
        from db.models import Summary
        all_summaries_result = await session.execute(
            select(Summary).where(
                Summary.date == today,
                Summary.period == period
            )
        )
        all_summaries = list(all_summaries_result.scalars().all())
        
    action = "Generated/refreshed" if replace_existing_summaries else "Generated"
    llm_usage = [
        usage
        for summary in summaries
        if isinstance((usage := getattr(summary, "_llm_usage", None)), dict)
    ]
    llm_total_tokens = sum(int(usage.get("total_tokens") or 0) for usage in llm_usage)
    llm_estimated_cost_usd = sum(
        float(usage.get("estimated_cost_usd") or 0)
        for usage in llm_usage
        if usage.get("estimated_cost_usd") is not None
    )
    logger.info(
        f"{action} {len(summaries)} summaries. Total for {period} today: {len(all_summaries)}",
        llm_total_tokens=llm_total_tokens,
        llm_estimated_cost_usd=round(llm_estimated_cost_usd, 8),
    )
    return StepResult(
        status="ok",
        message="Geração de resumos finalizada",
        metadata={
            "summaries": len(all_summaries),
            "new_summaries": len(summaries),
            "llm_total_tokens": llm_total_tokens,
            "llm_estimated_cost_usd": round(llm_estimated_cost_usd, 8),
            "llm_usage": llm_usage,
        },
        payload=all_summaries,
    )


async def _step_deliver(subscribers: list[Subscriber], summaries, period: str) -> StepResult:
    """Step 5: Deliver digest to active subscribers via WhatsApp."""
    sent_count = await send_digest(subscribers, summaries, period)
    logger.info(f"Sent {sent_count} messages to {len(subscribers)} subscribers")
    return StepResult(
        status="ok",
        message="Entrega finalizada",
        metadata={"subscribers": len(subscribers), "messagesSent": sent_count},
        payload=sent_count,
    )


# ---------------------------------------------------------------------------
# Pipeline orchestrator
# ---------------------------------------------------------------------------


async def run_pipeline(
    period: str,
    request_id: str | None = None,
    *,
    replace_existing_summaries: bool = False,
) -> None:
    """Run the full news collection, processing, and delivery pipeline."""
    async with _pipeline_lock:
        await _run_pipeline_impl(
            period,
            request_id=request_id,
            replace_existing_summaries=replace_existing_summaries,
        )


async def _run_pipeline_impl(
    period: str,
    request_id: str | None = None,
    *,
    replace_existing_summaries: bool = False,
) -> None:
    """Short orchestrator — chains pipeline steps via execute_step."""
    run = await create_pipeline_run(period)
    await record_pipeline_event(
        run.id, "pipeline", "started",
        f"Pipeline {period} iniciado",
        {
            **({"requestId": request_id} if request_id else {}),
            "replaceExistingSummaries": replace_existing_summaries,
        },
    )

    try:
        # Step 1: Collect
        result = await execute_step(
            run.id, "fetch_feeds", _step_fetch_feeds,
            timeout_seconds=TIMEOUT_FETCH_FEEDS,
            start_message="Coleta de feeds iniciada",
            start_metadata={"timeoutSeconds": TIMEOUT_FETCH_FEEDS},
        )
        if result.status == "failed":
            await update_pipeline_run(run.id, "failed", error_log=result.error_log)
            await alert_admin(f"Pipeline {period}: {result.error_log}")
            return

        entries = result.payload
        if not entries:
            logger.warning(f"No articles collected in {period} pipeline")
            await record_pipeline_event(run.id, "pipeline", "ok", "Nenhuma notícia coletada")
            await update_pipeline_run(run.id, "completed", articles_collected=0)
            return

        # Step 2: Deduplicate
        result = await execute_step(
            run.id, "deduplicate", lambda: _step_deduplicate(entries),
            start_message="Deduplicação iniciada",
            start_metadata={"entries": len(entries)},
        )
        if result.status == "failed":
            await update_pipeline_run(run.id, "failed", error_log=result.error_log)
            return

        new_entries = result.payload

        # Step 3: Extract and save
        result = await execute_step(
            run.id, "extract_articles", lambda: _step_extract_and_save_all(new_entries),
            start_message="Extração de conteúdo iniciada",
            start_metadata={"newEntries": len(new_entries), "maxConcurrent": MAX_CONCURRENT_EXTRACTIONS},
        )
        # extract_articles always returns "ok" (partial failures are logged in metadata)
        articles = result.payload
        await update_pipeline_run(run.id, "running", articles_collected=len(articles))

        if not articles and not replace_existing_summaries:
            logger.warning("No articles with content to process")
            await record_pipeline_event(run.id, "pipeline", "ok", "Nenhum artigo com conteúdo para processar")
            await update_pipeline_run(run.id, "completed")
            return
        if not articles:
            logger.info("No new articles with content; refreshing summaries from stored articles")

        # Step 4: Summarize
        categories_present = (
            set(VALID_CATEGORIES)
            if replace_existing_summaries
            else {a.category for a in articles}
        )
        summarize_timeout = max(
            TIMEOUT_SUMMARIZE,
            len(categories_present) * TIMEOUT_SUMMARIZE_PER_CATEGORY,
        )
        result = await execute_step(
            run.id,
            "summarize",
            lambda: _step_summarize(
                articles,
                period,
                replace_existing_summaries=replace_existing_summaries,
            ),
            timeout_seconds=summarize_timeout,
            start_message="Geração de resumos iniciada",
            start_metadata={
                "articles": len(articles),
                "categories": len(categories_present),
                "timeoutSeconds": summarize_timeout,
                "replaceExistingSummaries": replace_existing_summaries,
            },
        )
        if result.status == "failed":
            await update_pipeline_run(run.id, "failed", error_log=result.error_log)
            await alert_admin(f"Pipeline {period}: {result.error_log}")
            return

        summaries = result.payload
        await update_pipeline_run(run.id, "running", summaries_generated=len(summaries))

        if not summaries:
            logger.warning("No summaries generated")
            await record_pipeline_event(run.id, "pipeline", "ok", "Nenhum resumo gerado")
            await update_pipeline_run(run.id, "completed")
            return

        # Step 5: Deliver
        try:
            async with async_session() as session:
                sub_result = await session.execute(
                    select(Subscriber).where(Subscriber.active == True)  # noqa: E712
                )
                subscribers = list(sub_result.scalars().all())
        except SQLAlchemyError as exc:
            logger.error(f"Failed to fetch subscribers: {exc}")
            await update_pipeline_run(run.id, "failed", error_log="DB error fetching subscribers")
            return

        if not subscribers:
            logger.info("No active subscribers to deliver to")
            await update_pipeline_run(run.id, "completed")
            return

        result = await execute_step(
            run.id, "delivery", lambda: _step_deliver(subscribers, summaries, period),
            start_message="Entrega iniciada",
        )
        if result.status == "failed":
            await update_pipeline_run(run.id, "failed", error_log=result.error_log)
            await alert_admin(f"Pipeline {period}: {result.error_log}")
            return

        await record_pipeline_event(run.id, "pipeline", "ok", f"Pipeline {period} concluído")
        await update_pipeline_run(run.id, "completed", messages_sent=result.payload)

    except asyncio.CancelledError:
        logger.warning(f"{period} pipeline was cancelled")
        await record_pipeline_event(run.id, "pipeline", "failed", "Pipeline cancelled")
        await update_pipeline_run(run.id, "failed", error_log="Pipeline cancelled")
    except Exception as exc:
        logger.exception(f"{period} pipeline failed with unexpected error")
        await record_pipeline_event(run.id, "pipeline", "failed", f"Unexpected: {type(exc).__name__}")
        await update_pipeline_run(run.id, "failed", error_log=f"Unexpected: {type(exc).__name__}")
        await alert_admin(f"Pipeline {period} unexpected error: {type(exc).__name__}: {exc}")
    finally:
        await finish_pipeline_run(run.id)


# ---------------------------------------------------------------------------
# Period convenience wrappers
# ---------------------------------------------------------------------------


async def run_morning_pipeline(
    request_id: str | None = None,
    *,
    replace_existing_summaries: bool = False,
) -> None:
    await run_pipeline(
        "morning",
        request_id=request_id,
        replace_existing_summaries=replace_existing_summaries,
    )


async def run_midday_pipeline(
    request_id: str | None = None,
    *,
    replace_existing_summaries: bool = False,
) -> None:
    await run_pipeline(
        "midday",
        request_id=request_id,
        replace_existing_summaries=replace_existing_summaries,
    )


async def run_afternoon_pipeline(
    request_id: str | None = None,
    *,
    replace_existing_summaries: bool = False,
) -> None:
    await run_pipeline(
        "afternoon",
        request_id=request_id,
        replace_existing_summaries=replace_existing_summaries,
    )


async def run_evening_pipeline(
    request_id: str | None = None,
    *,
    replace_existing_summaries: bool = False,
) -> None:
    await run_pipeline(
        "evening",
        request_id=request_id,
        replace_existing_summaries=replace_existing_summaries,
    )


# ---------------------------------------------------------------------------
# Maintenance jobs
# ---------------------------------------------------------------------------


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
        await alert_admin(f"Feeds inativos: {feed_names}")


# ---------------------------------------------------------------------------
# Article extraction helper
# ---------------------------------------------------------------------------


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
