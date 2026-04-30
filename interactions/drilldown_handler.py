from typing import Any

import asyncio
import re

import structlog
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from db.engine import async_session
from db.models import NewsArticle, Summary
from processor.llm_client import get_llm_client
from processor.prompts import DRILLDOWN_SYSTEM_PROMPT

logger = structlog.get_logger()

MAX_DRILLDOWN_SOURCE_ARTICLES = 4
MAX_DRILLDOWN_ARTICLE_CHARS = 1400
MAX_DRILLDOWN_RESPONSE_CHARS = 2400
DRILLDOWN_TIMEOUT_SECONDS = 55.0
DRILLDOWN_MAX_TOKENS = 1600


def _normalize_command(command: str) -> str:
    first_token = (command or "").strip().lower().split(maxsplit=1)[0]
    if not first_token:
        return ""
    if first_token.startswith("!"):
        return first_token
    return f"!{first_token}"


def _text_value(item: dict[str, Any], key: str) -> str:
    value = item.get(key)
    return value.strip() if isinstance(value, str) else ""


def _source_article_ids(item: dict[str, Any]) -> list[int]:
    raw_ids = item.get("source_article_ids")
    if not isinstance(raw_ids, list):
        return []

    article_ids: list[int] = []
    for raw_id in raw_ids:
        if isinstance(raw_id, int) and raw_id not in article_ids:
            article_ids.append(raw_id)
    return article_ids


def _article_source_name(article: NewsArticle) -> str:
    source = getattr(article, "source", None)
    return str(getattr(source, "name", "") or "Fonte").strip()


def _render_source_articles(source_articles: list[NewsArticle]) -> str:
    if not source_articles:
        return ""

    lines: list[str] = []
    seen_titles: set[str] = set()
    for article in source_articles:
        title = " ".join(str(getattr(article, "title", "") or "").split()).strip()
        if not title:
            continue
        title_key = title.lower()
        if title_key in seen_titles:
            continue
        seen_titles.add(title_key)
        lines.append(f"- {_article_source_name(article)}: {title}")
        if len(lines) >= 4:
            break

    if not lines:
        return ""
    return "Base usada:\n" + "\n".join(lines)


def _compact_text(text: str, max_chars: int) -> str:
    compact = re.sub(r"\s+", " ", text or "").strip()
    if len(compact) <= max_chars:
        return compact
    return compact[:max_chars].rsplit(" ", 1)[0].rstrip(" ,.;:") + "..."


def _article_title(article: NewsArticle) -> str:
    return " ".join(str(getattr(article, "title", "") or "").split()).strip()


def _format_published_at(article: NewsArticle) -> str:
    published_at = getattr(article, "published_at", None)
    if not published_at:
        return "não informado"
    return published_at.isoformat()


def _article_context_block(index: int, article: NewsArticle) -> str:
    content = _compact_text(str(getattr(article, "raw_content", "") or ""), MAX_DRILLDOWN_ARTICLE_CHARS)
    return "\n".join(
        [
            f"ARTIGO {index}",
            f"Fonte: {_article_source_name(article)}",
            f"Título: {_article_title(article)}",
            f"Publicado em: {_format_published_at(article)}",
            f"Conteúdo: {content or 'Sem texto extraído.'}",
        ]
    )


def _build_drilldown_user_prompt(
    item: dict[str, Any],
    source_articles: list[NewsArticle],
) -> str:
    item_lines = [
        f"Título do item: {_text_value(item, 'title')}",
        f"O que aconteceu salvo: {_text_value(item, 'what_happened')}",
        f"Por que importa salvo: {_text_value(item, 'why_it_matters')}",
        f"Próximo ponto salvo: {_text_value(item, 'watchlist')}",
        f"Comando: {_text_value(item, 'command_hint')}",
    ]
    article_blocks = [
        _article_context_block(index, article)
        for index, article in enumerate(source_articles[:MAX_DRILLDOWN_SOURCE_ARTICLES], start=1)
    ]
    return "\n".join(
        [
            "ITEM SELECIONADO",
            *item_lines,
            "",
            "ARTIGOS-FONTE",
            "\n\n".join(article_blocks),
            "",
            "Gere o aprofundamento final para WhatsApp.",
            "Use exatamente os blocos pedidos e uma frase por bloco de análise.",
        ]
    )


def _normalize_llm_drilldown_response(text: str) -> str | None:
    response = (text or "").strip()
    if not response:
        return None

    response = re.sub(r"```(?:\w+)?", "", response).replace("```", "")
    response = re.sub(r"\*\*(.*?)\*\*", r"*\1*", response)
    response = re.sub(r"https?://\S+", "", response)
    response = re.sub(r"[ \t]+", " ", response)
    response = re.sub(r"\n{3,}", "\n\n", response).strip()
    if not response:
        return None
    if "base usada:" not in response.lower():
        logger.warning("LLM drilldown response missing source block")
        return None

    if len(response) <= MAX_DRILLDOWN_RESPONSE_CHARS:
        return response

    trimmed = response[:MAX_DRILLDOWN_RESPONSE_CHARS].rsplit("\n", 1)[0].strip()
    if len(trimmed) < 400:
        trimmed = response[:MAX_DRILLDOWN_RESPONSE_CHARS].rsplit(" ", 1)[0].strip()
    return trimmed.rstrip(" ,.;:") + "..."


async def _build_llm_drilldown_response(
    command: str,
    item: dict[str, Any],
    source_articles: list[NewsArticle],
) -> str | None:
    usable_articles = [
        article
        for article in source_articles
        if _article_title(article) or str(getattr(article, "raw_content", "") or "").strip()
    ]
    if not usable_articles:
        return None

    try:
        client = get_llm_client()
        response = await asyncio.wait_for(
            client.chat_async_with_usage(
                system_prompt=DRILLDOWN_SYSTEM_PROMPT,
                user_prompt=_build_drilldown_user_prompt(item, usable_articles),
                max_tokens=DRILLDOWN_MAX_TOKENS,
            ),
            timeout=DRILLDOWN_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        logger.warning(
            "Failed to generate LLM drilldown; using deterministic fallback",
            command=command,
            error=str(exc),
        )
        return None

    usage = getattr(response, "usage", None)
    if usage:
        logger.info("Drilldown LLM usage", command=command, **usage.to_metadata())

    return _normalize_llm_drilldown_response(getattr(response, "content", ""))


def _render_item_drilldown(
    item: dict[str, Any],
    source_articles: list[NewsArticle] | None = None,
) -> str | None:
    title = _text_value(item, "title")
    what_happened = _text_value(item, "what_happened")
    why_it_matters = _text_value(item, "why_it_matters")
    watchlist = _text_value(item, "watchlist")

    parts: list[str] = []
    if title:
        parts.append(f"*{title}*")

    if what_happened:
        parts.append(f"O que aconteceu: {what_happened}")
    if why_it_matters:
        parts.append(f"Por que importa: {why_it_matters}")
    if watchlist:
        parts.append(f"Próximo ponto: {watchlist}")

    source_block = _render_source_articles(source_articles or [])
    if source_block:
        parts.append(source_block)

    return "\n\n".join(parts) if parts else None


async def build_drilldown_response_for_command(command: str) -> str | None:
    normalized_command = _normalize_command(command)
    if not normalized_command:
        return None

    async with async_session() as session:
        result = await session.execute(select(Summary).order_by(Summary.created_at.desc()).limit(25))
        summaries = result.scalars().all()

    matches: list[dict[str, Any]] = []
    for summary in summaries:
        key_takeaways = summary.key_takeaways if isinstance(summary.key_takeaways, dict) else {}
        items = key_takeaways.get("items", [])
        if not isinstance(items, list):
            continue

        for item in items:
            if not isinstance(item, dict):
                continue
            if _normalize_command(_text_value(item, "command_hint")) == normalized_command:
                matches.append(item)

    if len(matches) != 1:
        return None

    item = matches[0]
    source_ids = _source_article_ids(item)
    source_articles: list[NewsArticle] = []
    if source_ids:
        async with async_session() as session:
            result = await session.execute(
                select(NewsArticle)
                .options(selectinload(NewsArticle.source))
                .where(NewsArticle.id.in_(source_ids))
            )
            articles_by_id = {article.id: article for article in result.scalars().all()}
        source_articles = [
            article
            for article_id in source_ids
            if (article := articles_by_id.get(article_id)) is not None
        ]

    llm_response = await _build_llm_drilldown_response(normalized_command, item, source_articles)
    if llm_response:
        return llm_response

    return _render_item_drilldown(item, source_articles)
