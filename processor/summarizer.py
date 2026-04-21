import datetime
import asyncio

import structlog
from pydantic import BaseModel, Field, field_validator, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from config.settings import settings
from db.engine import async_session
from db.models import VALID_CATEGORIES, NewsArticle, Summary
from processor.categorizer import validate_category, validate_period
from processor.llm_client import get_llm_client
from processor.prompts import ARTICLE_BLOCK_TEMPLATE, SYSTEM_PROMPT_SUMMARY, USER_PROMPT_TEMPLATE

logger = structlog.get_logger()

MAX_ARTICLES_PER_CATEGORY = 12


class SummaryOutput(BaseModel):
    category: str
    period: str
    header: str
    bullets: list[str] = Field(min_length=3, max_length=5)
    insight: str
    full_summary_text: str = Field(max_length=4000)
    source_urls: list[str]

    @field_validator("category")
    @classmethod
    def validate_cat(cls, v: str) -> str:
        return validate_category(v)

    @field_validator("period")
    @classmethod
    def validate_per(cls, v: str) -> str:
        return validate_period(v)


async def generate_summaries_for_category(
    articles: list[NewsArticle],
    period: str,
    model_override: str | None = None,
) -> Summary | None:
    """Generate a summary for a batch of articles in the same category.
    
    Uses pessimistic locking to prevent race conditions when multiple processes
    try to create summaries for the same category/period/date simultaneously.
    """
    if not articles:
        return None

    category = articles[0].category
    today = datetime.date.today()

    client = get_llm_client()

    # Re-fetch articles with source eagerly loaded to avoid DetachedInstanceError
    article_ids = [a.id for a in articles]
    async with async_session() as session:
        result = await session.execute(
            select(NewsArticle)
            .options(selectinload(NewsArticle.source))
            .where(NewsArticle.id.in_(article_ids))
        )
        loaded_articles = result.scalars().all()

    # Build user prompt with article content
    articles_text_parts = []
    for i, article in enumerate(loaded_articles, 1):
        content = (article.raw_content or "")[:3000]
        articles_text_parts.append(
            ARTICLE_BLOCK_TEMPLATE.format(
                index=i,
                title=article.title,
                source=article.source.name if article.source else "Unknown",
                published_at=article.published_at.strftime("%Y-%m-%d %H:%M"),
                content=content,
            )
        )

    user_prompt = USER_PROMPT_TEMPLATE.format(
        period=period.upper(),
        category=category,
        articles_text="\n\n".join(articles_text_parts),
    )

    # Call LLM to generate summary
    try:
        result = await client.chat_json_async(
            SYSTEM_PROMPT_SUMMARY,
            user_prompt,
            model_override=model_override,
        )
        validated = SummaryOutput(**result)
    except ValueError as e:
        logger.error(f"LLM returned invalid JSON for {category}: {e}")
        return None
    except ValidationError as e:
        logger.error(f"LLM output failed Pydantic validation for {category}: {e}")
        return None
    except Exception as e:
        logger.error(
            f"Failed to generate summary for {category}: {type(e).__name__}: {e}"
        )
        return None

    # Save to database with race condition protection
    # Use pessimistic locking (for_update) to ensure atomicity
    async with async_session() as session:
        try:
            # Check if summary already exists with lock
            existing = await session.execute(
                select(Summary)
                .where(
                    Summary.category == category,
                    Summary.period == period,
                    Summary.date == today,
                )
                .with_for_update()  # Pessimistic lock
            )
            if existing.scalar_one_or_none():
                logger.info(
                    f"Summary for {category}/{period} on {today} already exists "
                    f"(created by another process), skipping"
                )
                return None

            # Create new summary
            summary = Summary(
                category=validated.category,
                period=validated.period,
                date=datetime.date.today(),
                summary_text=validated.full_summary_text,
                key_takeaways={
                    "bullets": validated.bullets,
                    "insight": validated.insight
                },
                source_article_ids=[a.id for a in loaded_articles],
                model_used=model_override or client.model_name,
            )
            session.add(summary)
            await session.flush()  # Flush to get the ID
            await session.refresh(summary)

            # Mark articles as processed
            for article_id in article_ids:
                db_article = await session.get(NewsArticle, article_id)
                if db_article:
                    db_article.processed = True
                    db_article.summary_id = summary.id

            await session.commit()
            logger.info(f"Created summary for {category}/{period}, marked {len(article_ids)} articles as processed")
            return summary

        except IntegrityError as e:
            logger.warning(
                f"Integrity error creating summary for {category}/{period} "
                f"(likely duplicate from race condition): {e}"
            )
            await session.rollback()
            # Return None - summary was created by another process
            return None
        except SQLAlchemyError as e:
            logger.error(
                f"Database error creating summary for {category}/{period}: "
                f"{type(e).__name__}: {e}"
            )
            await session.rollback()
            return None


async def generate_all_summaries(articles: list[NewsArticle], period: str) -> list[Summary]:
    """Generate summaries for all categories present in the article list."""
    by_category: dict[str, list[NewsArticle]] = {}
    for article in articles:
        by_category.setdefault(article.category, []).append(article)

    model_pool = _build_summary_model_pool()
    model_semaphores = {
        model_name: asyncio.Semaphore(1) for model_name in model_pool
    }

    async def _run_category(
        category: str,
        cat_articles: list[NewsArticle],
        model_name: str,
    ) -> Summary | None:
        async with model_semaphores[model_name]:
            logger.info(
                f"Generating summary for {category} "
                f"({len(cat_articles)} articles) with {model_name}"
            )
            return await generate_summaries_for_category(
                cat_articles,
                period,
                model_override=model_name,
            )

    tasks: list[asyncio.Task[Summary | None]] = []
    model_index = 0
    for category in VALID_CATEGORIES:
        cat_articles = by_category.get(category, [])
        if not cat_articles:
            continue

        cat_articles = sorted(
            cat_articles,
            key=lambda article: article.published_at,
            reverse=True,
        )[:MAX_ARTICLES_PER_CATEGORY]

        model_name = model_pool[model_index % len(model_pool)]
        model_index += 1
        tasks.append(asyncio.create_task(_run_category(category, cat_articles, model_name)))

    results = await asyncio.gather(*tasks)
    return [summary for summary in results if summary]


def _build_summary_model_pool() -> list[str]:
    models = [settings.llm_model_primary]
    secondary = settings.llm_model_secondary.strip() if settings.llm_model_secondary else ""
    if secondary and secondary not in models:
        models.append(secondary)
    return models
