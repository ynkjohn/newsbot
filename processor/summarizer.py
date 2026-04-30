import asyncio
import re

import structlog
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import selectinload

from config.settings import settings
from config.time_utils import local_today, utc_now
from db.engine import async_session
from db.models import VALID_CATEGORIES, NewsArticle, Summary
from processor.categorizer import validate_category, validate_period
from processor.llm_client import LLMUsage, get_llm_client
from processor.prompts import ARTICLE_BLOCK_TEMPLATE, SYSTEM_PROMPT_SUMMARY, USER_PROMPT_TEMPLATE
from processor.summary_format import (
    SECTION_TITLES,
    build_summary_header,
    build_takeaways_payload,
    render_summary_text,
)

logger = structlog.get_logger()

MAX_ARTICLES_PER_CATEGORY = 12
MAX_ARTICLES_PER_EVENT = 2

CATEGORY_POSITIVE_TERMS = {
    "politica-brasil": {
        "alcolumbre",
        "bolsonaro",
        "camara",
        "câmara",
        "congresso",
        "deputado",
        "deputados",
        "dosimetria",
        "governo",
        "haddad",
        "lula",
        "messias",
        "ministro",
        "motta",
        "pec",
        "pf",
        "pl ",
        "politica",
        "política",
        "senado",
        "senador",
        "stf",
        "tarcisio",
        "tarcísio",
        "tse",
        "veto",
    },
    "economia-brasil": {
        "banco central",
        "bc ",
        "combustiveis",
        "combustíveis",
        "cooperativas",
        "dolar",
        "dólar",
        "energia",
        "fiis",
        "fundos",
        "ibovespa",
        "imposto",
        "juros",
        "mercosul",
        "petrobras",
        "petroleo",
        "petróleo",
        "selic",
        "tarifas",
        "vale",
    },
}

CATEGORY_NEGATIVE_TERMS = {
    "politica-brasil": {
        "ator",
        "atriz",
        "celebridades",
        "cinema",
        "corpo",
        "diabo veste prada",
        "dor no corpo",
        "emagrecimento",
        "entretenimento",
        "esportes",
        "fãs",
        "fezes",
        "filme",
        "futebol",
        "matar rivais",
        "neymar",
        "palmeiras",
        "proteina",
        "proteína",
        "saude",
        "saúde",
        "santos",
        "signos",
    },
    "economia-brasil": {
        "cultivo",
        "dicas",
        "poda",
    },
}

SOURCE_PRIORITY = {
    "Agencia Brasil Politica": 4,
    "G1 Política": 5,
    "Congresso em Foco": 4,
    "Camara dos Deputados Politica": 4,
    "Repórter Brasil": 3,
    "Metropoles": 1,
    "Agencia Brasil Economia": 4,
    "G1 Economia": 5,
    "Camara dos Deputados Economia": 4,
    "InfoMoney": 4,
    "Suno": 3,
    "Investing.com Brasil": 1,
}

CATEGORY_EVENT_TERMS = {
    "politica-brasil": {
        "dosimetria": {"dosimetria"},
        "messias-stf": {"messias"},
        "desoneracao": {"desoneração", "desoneracao"},
    },
}

ARTICLE_TOKEN_STOPWORDS = {
    "ainda",
    "apos",
    "após",
    "como",
    "com",
    "contra",
    "da",
    "das",
    "de",
    "do",
    "dos",
    "em",
    "entre",
    "esta",
    "está",
    "para",
    "pela",
    "pelo",
    "que",
    "sobre",
    "uma",
    "veja",
}


class SummarySection(BaseModel):
    key: str
    title: str = ""
    content: str = Field(min_length=40, max_length=1200)

    @field_validator("key")
    @classmethod
    def normalize_key(cls, value: str) -> str:
        key = str(value or "").strip().lower()
        if key not in SECTION_TITLES:
            raise ValueError(f"invalid section key: {value}")
        return key

    @model_validator(mode="after")
    def set_fallback_title(self) -> "SummarySection":
        if not self.title or not self.title.strip():
            self.title = SECTION_TITLES.get(self.key, "Seção")
        return self


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

    @field_validator("importance_score", mode="before")
    @classmethod
    def normalize_importance_score(cls, value: object) -> int:
        if value is None or value == "":
            return 3

        if isinstance(value, str):
            label_scores = {
                "critical": 5,
                "high": 5,
                "medium": 3,
                "low": 1,
            }
            normalized = value.strip().lower()
            if normalized in label_scores:
                return label_scores[normalized]
            value = normalized.replace(",", ".")

        try:
            score = int(float(value))
        except (TypeError, ValueError):
            return 3

        return max(1, min(5, score))

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

    @field_validator("bullets", mode="before")
    @classmethod
    def limit_bullets(cls, value: object) -> object:
        if isinstance(value, list):
            return value[:5]
        return value

    @field_validator("sections", mode="before")
    @classmethod
    def limit_sections(cls, value: object) -> object:
        if isinstance(value, list):
            return value[:4]
        return value

    @field_validator("insight")
    @classmethod
    def validate_insight(cls, value: str) -> str:
        text = " ".join(str(value or "").split()).strip()
        if len(text) < 30:
            raise ValueError("insight too short")
        return text


def _item_payloads_with_article_ids(
    items: list[DigestItemOutput],
    loaded_articles: list[NewsArticle],
) -> list[dict]:
    article_id_by_index = {
        index: article.id
        for index, article in enumerate(loaded_articles, start=1)
        if article.id is not None
    }
    valid_article_ids = set(article_id_by_index.values())

    payloads: list[dict] = []
    for item in items:
        payload = item.model_dump()
        source_article_ids = [
            article_id
            for article_id in payload.get("source_article_ids", [])
            if isinstance(article_id, int) and article_id in valid_article_ids
        ]
        for source_index in payload.get("source_indexes", []):
            article_id = article_id_by_index.get(source_index)
            if article_id and article_id not in source_article_ids:
                source_article_ids.append(article_id)
        payload["source_article_ids"] = source_article_ids
        payloads.append(payload)

    return payloads


def _attach_llm_usage(summary: Summary, usage: LLMUsage | None) -> None:
    if not usage:
        return
    summary.token_count = usage.total_tokens
    setattr(summary, "_llm_usage", usage.to_metadata())


def _article_text(article: NewsArticle) -> str:
    return " ".join(
        [
            str(getattr(article, "title", "") or ""),
            str(getattr(article, "url", "") or ""),
        ]
    ).lower()


def _article_title(article: NewsArticle) -> str:
    return str(getattr(article, "title", "") or "").lower()


def _source_name(article: NewsArticle) -> str:
    source = getattr(article, "source", None)
    return str(getattr(source, "name", "") or "")


def _article_relevance_score(article: NewsArticle, category: str) -> int:
    text = _article_text(article)
    source_score = SOURCE_PRIORITY.get(_source_name(article), 0)
    positive_terms = CATEGORY_POSITIVE_TERMS.get(category, set())
    negative_terms = CATEGORY_NEGATIVE_TERMS.get(category, set())
    positive_score = sum(3 for term in positive_terms if term in text)
    negative_score = sum(8 for term in negative_terms if term in text)
    return source_score + positive_score - negative_score


def _article_has_category_signal(article: NewsArticle, category: str) -> bool:
    text = _article_text(article)
    source_score = SOURCE_PRIORITY.get(_source_name(article), 0)
    positive_terms = CATEGORY_POSITIVE_TERMS.get(category, set())
    return source_score >= 3 or any(term in text for term in positive_terms)


def _article_event_key(article: NewsArticle, category: str) -> str | None:
    text = _article_text(article)
    for event_key, terms in CATEGORY_EVENT_TERMS.get(category, {}).items():
        if any(term in text for term in terms):
            return event_key
    return None


def _article_topic_tokens(article: NewsArticle) -> set[str]:
    tokens = set(re.findall(r"[a-z0-9áàâãéêíóôõúç]+", _article_title(article)))
    return {
        token
        for token in tokens
        if len(token) >= 4 and token not in ARTICLE_TOKEN_STOPWORDS
    }


def _articles_are_similar(left: NewsArticle, right: NewsArticle) -> bool:
    left_tokens = _article_topic_tokens(left)
    right_tokens = _article_topic_tokens(right)
    if not left_tokens or not right_tokens:
        return False

    overlap = len(left_tokens & right_tokens) / min(len(left_tokens), len(right_tokens))
    return overlap >= 0.4


def _diversify_scored_articles(
    scored: list[tuple[int, NewsArticle]],
    category: str,
) -> list[tuple[int, NewsArticle]]:
    selected: list[tuple[int, NewsArticle]] = []
    deferred: list[tuple[int, NewsArticle]] = []
    event_counts: dict[str, int] = {}

    for score, article in scored:
        event_key = _article_event_key(article, category)
        repeated_event = bool(
            event_key and event_counts.get(event_key, 0) >= MAX_ARTICLES_PER_EVENT
        )
        similar_articles = sum(
            1 for _selected_score, selected_article in selected
            if _articles_are_similar(article, selected_article)
        )
        if repeated_event or similar_articles >= MAX_ARTICLES_PER_EVENT:
            deferred.append((score, article))
            continue

        selected.append((score, article))
        if event_key:
            event_counts[event_key] = event_counts.get(event_key, 0) + 1
        if len(selected) >= MAX_ARTICLES_PER_CATEGORY:
            return selected

    for item in deferred:
        if len(selected) >= MAX_ARTICLES_PER_CATEGORY:
            break
        selected.append(item)
    return selected


def _select_articles_for_summary(
    category_articles: list[NewsArticle],
    category: str,
) -> list[NewsArticle]:
    scored = [
        (_article_relevance_score(article, category), article)
        for article in category_articles
    ]

    if category in CATEGORY_POSITIVE_TERMS:
        relevant = [
            (score, article)
            for score, article in scored
            if score > 0 and _article_has_category_signal(article, category)
        ]
        if relevant:
            scored = relevant

    scored.sort(
        key=lambda item: (
            item[0],
            getattr(item[1], "published_at", None) or utc_now(),
            getattr(item[1], "id", 0) or 0,
        ),
        reverse=True,
    )
    diversified = _diversify_scored_articles(scored, category)
    return [article for _score, article in diversified[:MAX_ARTICLES_PER_CATEGORY]]


async def generate_summaries_for_category(
    articles: list[NewsArticle],
    period: str,
    model_override: str | None = None,
    replace_existing: bool = False,
) -> Summary | None:
    if not articles:
        return None

    category = validate_category(articles[0].category)
    today = local_today()
    client = get_llm_client()

    article_ids = [article.id for article in articles]
    async with async_session() as session:
        if not replace_existing:
            existing = await session.execute(
                select(Summary).where(
                    Summary.category == category,
                    Summary.period == period,
                    Summary.date == today,
                )
            )
            if existing.scalar_one_or_none():
                logger.info(f"Summary for {category}/{period} on {today} already exists, skipping")
                return None

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
        result, llm_usage = await client.chat_json_async_with_usage(
            SYSTEM_PROMPT_SUMMARY,
            user_prompt,
            max_tokens=8192,
            model_override=model_override,
        )
        validated = SummaryOutput(**result)
    except ValidationError as exc:
        logger.error(f"LLM output failed Pydantic validation for {category}: {exc}")
        return None
    except ValueError as exc:
        logger.error(f"LLM returned invalid JSON for {category}: {exc}")
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
        items=_item_payloads_with_article_ids(validated.items, loaded_articles),
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
            summary = existing.scalar_one_or_none()
            replaced_existing = summary is not None
            source_article_ids = [article.id for article in loaded_articles]
            if summary:
                if not replace_existing:
                    logger.info(f"Summary for {category}/{period} on {today} already exists, skipping")
                    return None

                summary.summary_text = summary_text
                summary.key_takeaways = takeaways
                summary.source_article_ids = source_article_ids
                summary.model_used = model_override or client.model_name
                summary.created_at = utc_now()
                summary.sent_at = None
            else:
                summary = Summary(
                    category=validated.category,
                    period=validated.period,
                    date=today,
                    summary_text=summary_text,
                    key_takeaways=takeaways,
                    source_article_ids=source_article_ids,
                    model_used=model_override or client.model_name,
                )
                session.add(summary)

            _attach_llm_usage(summary, llm_usage)
            await session.flush()
            await session.refresh(summary)
            _attach_llm_usage(summary, llm_usage)

            await session.execute(
                update(NewsArticle)
                .where(NewsArticle.id.in_(article_ids))
                .values(processed=True, summary_id=summary.id)
            )

            await session.commit()
            action = "Replaced" if replaced_existing else "Created"
            logger.info(f"{action} summary for {category}/{period} with {len(article_ids)} articles")
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


async def generate_all_summaries(
    articles: list[NewsArticle],
    period: str,
    *,
    replace_existing: bool = False,
) -> list[Summary]:
    by_category: dict[str, list[NewsArticle]] = {}
    for article in articles:
        by_category.setdefault(validate_category(article.category), []).append(article)

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
                replace_existing=replace_existing,
            )

    tasks: list[asyncio.Task[Summary | None]] = []
    model_index = 0
    for category in VALID_CATEGORIES:
        category_articles = by_category.get(category, [])
        if not category_articles:
            continue

        trimmed_articles = _select_articles_for_summary(category_articles, category)
        model_name = model_pool[model_index % len(model_pool)]
        model_index += 1
        tasks.append(asyncio.create_task(run_category(category, trimmed_articles, model_name)))

    results = await asyncio.gather(*tasks)
    return [summary for summary in results if summary]


def _build_summary_model_pool() -> list[str]:
    from processor.llm_config import get_active_llm_config
    
    config = get_active_llm_config()
    models = [config.model]
    
    # Keeping secondary support if needed, but primary comes from dynamic config
    secondary = settings.llm_model_secondary.strip() if settings.llm_model_secondary else ""
    if secondary and secondary not in models:
        models.append(secondary)
    return models
