import asyncio

import structlog
from pydantic import BaseModel, Field, ValidationError, field_validator
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import selectinload

from config.settings import settings
from config.time_utils import local_today
from db.engine import async_session
from db.models import VALID_CATEGORIES, NewsArticle, Summary
from processor.categorizer import validate_category, validate_period
from processor.llm_client import get_llm_client
from processor.prompts import ARTICLE_BLOCK_TEMPLATE, SYSTEM_PROMPT_SUMMARY, USER_PROMPT_TEMPLATE
from processor.summary_format import (
    SECTION_TITLES,
    build_summary_header,
    build_takeaways_payload,
    render_summary_text,
)

logger = structlog.get_logger()

MAX_ARTICLES_PER_CATEGORY = 12


class SummarySection(BaseModel):
    key: str
    title: str
    content: str = Field(min_length=40, max_length=1200)

    @field_validator("key")
    @classmethod
    def normalize_key(cls, value: str) -> str:
        key = str(value or "").strip().lower()
        if key not in SECTION_TITLES:
            raise ValueError(f"invalid section key: {value}")
        return key

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str, info) -> str:
        section_key = info.data.get("key")
        fallback = SECTION_TITLES.get(section_key or "", "Seção")
        title = str(value or "").strip()
        return title or fallback


class DigestItemOutput(BaseModel):
    event_key: str
    title: str
    why_it_matters: str = Field(min_length=20, max_length=1200)
    what_happened: str = Field(min_length=20, max_length=1200)
    watchlist: str = Field(default="", max_length=1200)
    source_indexes: list[int] = Field(default_factory=list)
    source_article_ids: list[int] = Field(default_factory=list)
    importance: str = "medium"
    importance_score: int = Field(default=3, ge=1, le=5)
    novelty: str = "update"
    sentiment: str = "neutral"
    material_change: bool = False
    trust_status: str = "trusted"
    command_hint: str

    @field_validator("event_key", "title")
    @classmethod
    def validate_required_text(cls, value: str) -> str:
        text = " ".join(str(value or "").split()).strip()
        if not text:
            raise ValueError("required item text is empty")
        return text

    @field_validator("why_it_matters", "what_happened", "watchlist")
    @classmethod
    def normalize_long_text(cls, value: str) -> str:
        return " ".join(str(value or "").split()).strip()

    @field_validator("importance", "novelty", "sentiment", "trust_status")
    @classmethod
    def normalize_label(cls, value: str) -> str:
        return " ".join(str(value or "").split()).strip().lower()

    @field_validator("command_hint")
    @classmethod
    def validate_command_hint(cls, value: str) -> str:
        command = " ".join(str(value or "").split()).strip().lower()
        if (
            not command.startswith("!")
            or " " in command
            or len(command) < 2
            or len(command) > 50
            or command[1:].isdigit()
        ):
            raise ValueError("invalid command_hint")
        return command


class SummaryOutput(BaseModel):
    category: str
    period: str
    header: str
    bullets: list[str] = Field(min_length=3, max_length=5)
    insight: str = Field(min_length=30, max_length=600)
    sections: list[SummarySection] = Field(min_length=2, max_length=4)
    items: list[DigestItemOutput] = Field(default_factory=list)

    @field_validator("category")
    @classmethod
    def validate_cat(cls, value: str) -> str:
        return validate_category(value)

    @field_validator("period")
    @classmethod
    def validate_per(cls, value: str) -> str:
        return validate_period(value)

    @field_validator("header")
    @classmethod
    def validate_header(cls, value: str) -> str:
        text = " ".join(str(value or "").split()).strip()
        if len(text) < 8:
            raise ValueError("header too short")
        return text

    @field_validator("bullets")
    @classmethod
    def validate_bullets(cls, value: list[str]) -> list[str]:
        bullets: list[str] = []
        for bullet in value:
            text = " ".join(str(bullet or "").split()).strip()
            if len(text) < 12:
                raise ValueError("bullet too short")
            bullets.append(text)
        return bullets

    @field_validator("insight")
    @classmethod
    def validate_insight(cls, value: str) -> str:
        text = " ".join(str(value or "").split()).strip()
        if len(text) < 30:
            raise ValueError("insight too short")
        return text


async def generate_summaries_for_category(
    articles: list[NewsArticle],
    period: str,
    model_override: str | None = None,
) -> Summary | None:
    if not articles:
        return None

    category = articles[0].category
    today = local_today()
    client = get_llm_client()

    article_ids = [article.id for article in articles]
    async with async_session() as session:
        result = await session.execute(
            select(NewsArticle)
            .options(selectinload(NewsArticle.source))
            .where(NewsArticle.id.in_(article_ids))
        )
        loaded_articles = result.scalars().all()

    articles_text = []
    for index, article in enumerate(loaded_articles, start=1):
        articles_text.append(
            ARTICLE_BLOCK_TEMPLATE.format(
                index=index,
                title=article.title,
                source=article.source.name if article.source else "Fonte não identificada",
                published_at=article.published_at.strftime("%Y-%m-%d %H:%M"),
                content=(article.raw_content or "")[:3000],
            )
        )

    user_prompt = USER_PROMPT_TEMPLATE.format(
        period=period,
        category=category,
        articles_text="\n\n".join(articles_text),
    )

    try:
        result = await client.chat_json_async(
            SYSTEM_PROMPT_SUMMARY,
            user_prompt,
            model_override=model_override,
        )
        validated = SummaryOutput(**result)
    except ValueError as exc:
        logger.error(f"LLM returned invalid JSON for {category}: {exc}")
        return None
    except ValidationError as exc:
        logger.error(f"LLM output failed Pydantic validation for {category}: {exc}")
        return None
    except Exception as exc:
        logger.error(f"Failed to generate summary for {category}: {type(exc).__name__}: {exc}")
        return None

    header = build_summary_header(validated.category, validated.period, validated.header)
    takeaways = build_takeaways_payload(
        header=header,
        bullets=validated.bullets,
        insight=validated.insight,
        sections=[section.model_dump() for section in validated.sections],
        items=[item.model_dump() for item in validated.items],
    )
    summary_text = render_summary_text(validated.category, validated.period, takeaways)

    async with async_session() as session:
        try:
            existing = await session.execute(
                select(Summary)
                .where(
                    Summary.category == category,
                    Summary.period == period,
                    Summary.date == today,
                )
                .with_for_update()
            )
            if existing.scalar_one_or_none():
                logger.info(f"Summary for {category}/{period} on {today} already exists, skipping")
                return None

            summary = Summary(
                category=validated.category,
                period=validated.period,
                date=today,
                summary_text=summary_text,
                key_takeaways=takeaways,
                source_article_ids=[article.id for article in loaded_articles],
                model_used=model_override or client.model_name,
            )
            session.add(summary)
            await session.flush()
            await session.refresh(summary)

            await session.execute(
                update(NewsArticle)
                .where(NewsArticle.id.in_(article_ids))
                .values(processed=True, summary_id=summary.id)
            )

            await session.commit()
            logger.info(f"Created summary for {category}/{period} with {len(article_ids)} articles")
            return summary
        except IntegrityError as exc:
            logger.warning(
                f"Integrity error creating summary for {category}/{period} (likely duplicate): {exc}"
            )
            await session.rollback()
            return None
        except SQLAlchemyError as exc:
            logger.error(
                f"Database error creating summary for {category}/{period}: {type(exc).__name__}: {exc}"
            )
            await session.rollback()
            return None


async def generate_all_summaries(articles: list[NewsArticle], period: str) -> list[Summary]:
    by_category: dict[str, list[NewsArticle]] = {}
    for article in articles:
        by_category.setdefault(article.category, []).append(article)

    model_pool = _build_summary_model_pool()
    model_semaphores = {model_name: asyncio.Semaphore(1) for model_name in model_pool}

    async def run_category(
        category: str,
        category_articles: list[NewsArticle],
        model_name: str,
    ) -> Summary | None:
        async with model_semaphores[model_name]:
            logger.info(f"Generating summary for {category} ({len(category_articles)} articles) with {model_name}")
            return await generate_summaries_for_category(
                category_articles,
                period,
                model_override=model_name,
            )

    tasks: list[asyncio.Task[Summary | None]] = []
    model_index = 0
    for category in VALID_CATEGORIES:
        category_articles = by_category.get(category, [])
        if not category_articles:
            continue

        trimmed_articles = sorted(
            category_articles,
            key=lambda article: article.published_at,
            reverse=True,
        )[:MAX_ARTICLES_PER_CATEGORY]
        model_name = model_pool[model_index % len(model_pool)]
        model_index += 1
        tasks.append(asyncio.create_task(run_category(category, trimmed_articles, model_name)))

    results = await asyncio.gather(*tasks)
    return [summary for summary in results if summary]


def _build_summary_model_pool() -> list[str]:
    models = [settings.llm_model_primary]
    secondary = settings.llm_model_secondary.strip() if settings.llm_model_secondary else ""
    if secondary and secondary not in models:
        models.append(secondary)
    return models
