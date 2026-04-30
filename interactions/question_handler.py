import datetime
import re
import unicodedata
from collections import defaultdict, deque

import structlog
from sqlalchemy import desc, select
from sqlalchemy.orm import joinedload

from db.engine import async_session
from db.models import NewsArticle, Subscriber, Summary, UserInteraction
from interactions.messages import natural_question_fallback, question_processing_error
from processor.llm_client import get_llm_client
from processor.prompts import (
    RAG_SYSTEM_PROMPT,
    RAG_SYSTEM_PROMPT_GROUP_CONVERSATIONAL,
    RAG_SYSTEM_PROMPT_GROUP_FOLLOWUP,
    RAG_SYSTEM_PROMPT_GROUP_IMPACT,
    RAG_SYSTEM_PROMPT_GROUP_SINGLE,
)
from processor.summary_format import normalize_takeaways

logger = structlog.get_logger()
_GROUP_HISTORY: dict[str, deque[tuple[str, str]]] = defaultdict(lambda: deque(maxlen=6))


async def handle_question(phone_number: str, question: str, is_group: bool = False) -> str:
    normalized_question = _normalize_group_question(question) if is_group else question.strip()
    group_history = _get_group_history_text(phone_number) if is_group else ""
    is_followup = is_group and _is_followup_question(normalized_question)
    is_impact = is_group and _is_impact_question(normalized_question)

    retrieval_query = normalized_question
    if is_followup and group_history:
        retrieval_query = f"{group_history}\nPergunta atual: {normalized_question}"

    context = await _retrieve_context(retrieval_query)
    if not context:
        return natural_question_fallback()

    if is_group:
        prompt_template = (
            RAG_SYSTEM_PROMPT_GROUP_SINGLE
            if _is_single_headline_question(normalized_question)
            else (
                RAG_SYSTEM_PROMPT_GROUP_IMPACT
                if is_impact
                else (
                    RAG_SYSTEM_PROMPT_GROUP_FOLLOWUP
                    if is_followup
                    else RAG_SYSTEM_PROMPT_GROUP_CONVERSATIONAL
                )
            )
        )
        system_prompt = prompt_template.format(
            context=context,
            question=normalized_question,
            group_history=group_history or "Sem histórico recente.",
        )
        max_tokens = 170 if is_impact else 220
    else:
        conversation_history = await _retrieve_conversation_history(phone_number)
        system_prompt = RAG_SYSTEM_PROMPT.format(
            context=context,
            question=normalized_question,
            conversation_history=conversation_history or "Sem histórico recente.",
        )
        max_tokens = 500

    client = get_llm_client()
    try:
        response = await client.chat_async(
            system_prompt=system_prompt,
            user_prompt=normalized_question,
            max_tokens=max_tokens,
        )
        if not response or not response.strip():
            logger.warning("Empty response from LLM", question=question[:50], is_group=is_group)
            return question_processing_error()

        if is_group:
            return _normalize_group_response(response, normalized_question)

        return response.strip()
    except Exception as exc:
        logger.error("Failed to answer question", error=str(exc), is_group=is_group)
        return question_processing_error()


def _normalize_group_question(question: str) -> str:
    cleaned = re.sub(r"@\d+\b", " ", question or "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,:-")
    return cleaned or (question or "").strip()


def remember_group_interaction(group_jid: str, incoming: str, response: str) -> None:
    history = _GROUP_HISTORY[group_jid]
    normalized_incoming = _normalize_group_question(incoming)
    normalized_response = " ".join((response or "").split()).strip()
    history.append((normalized_incoming, normalized_response))


def _get_group_history_text(group_jid: str, max_turns: int = 4) -> str:
    turns = list(_GROUP_HISTORY.get(group_jid, []))[-max_turns:]
    if not turns:
        return ""

    parts: list[str] = []
    for incoming, response in turns:
        parts.append(f"Usuário: {incoming[:180]}")
        parts.append(f"Assistente: {response[:220]}")
    return "\n".join(parts)


def _is_single_headline_question(question: str) -> bool:
    lowered = (question or "").lower()
    patterns = (
        "principal noticia",
        "noticia principal",
        "noticia mais importante",
        "mais importante da noite",
        "maior destaque",
        "destaque da noite",
    )
    return any(pattern in lowered for pattern in patterns)


def _is_followup_question(question: str) -> bool:
    lowered = (question or "").lower()
    patterns = (
        "essa",
        "esse",
        "isso",
        "a 1",
        "a 2",
        "a 3",
        "a 4",
        "eu digo",
        "mas qual",
        "mas se",
        "qual empresa",
        "qual pais",
        "como assim",
        "como nos coloca",
    )
    return any(pattern in lowered for pattern in patterns)


def _is_yes_no_question(question: str) -> bool:
    lowered = (question or "").strip().lower()
    return lowered.startswith(
        ("foi ", "era ", "e ", "eh ", "tem ", "teve ", "sera ", "seria ", "isso ", "essa ", "esse ")
    )


def _is_impact_question(question: str) -> bool:
    lowered = (question or "").lower()
    patterns = (
        "impacto",
        "impactos",
        "efeito",
        "efeitos",
        "consequencia",
        "consequencias",
        "por que importa",
        "o que isso muda",
        "muda o que",
        "qual a importancia",
        "qual a relevancia",
    )
    return any(pattern in lowered for pattern in patterns)


def _normalize_group_response(response: str, question: str) -> str:
    compact = " ".join((response or "").split()).strip()
    compact = re.sub(r"^\s*destaques?[^:]*:\s*", "", compact, flags=re.IGNORECASE)
    compact = re.sub(r"\b\d+[.)]\s*", "", compact)
    compact = re.sub(r"\s*[-•▪]\s*", " ", compact)
    if not _is_yes_no_question(question):
        compact = re.sub(r"^(sim|nao),\s*", "", compact, flags=re.IGNORECASE)
    compact = _normalize_source_phrase(compact)
    compact = _soften_group_tone(compact)
    compact = re.sub(r"\s+", " ", compact).strip()
    compact = _limit_group_sentences(compact, max_sentences=2)
    if len(compact) <= 190:
        return compact
    trimmed = compact[:190].rsplit(" ", 1)[0].rstrip(" ,.;:")
    return trimmed + "..."


def _normalize_source_phrase(text: str) -> str:
    match = re.search(r"(?:\(?\s*fonte:\s*([^)]+?)\)?\.?\s*)$", text, flags=re.IGNORECASE)
    if not match:
        return text

    source = match.group(1).strip(" .)")
    if _is_generic_source_label(source):
        return text[: match.start()].rstrip(" ,.;:")

    rewritten = text[: match.start()].rstrip(" ,.;:")
    if rewritten:
        rewritten += ". "
    return f"{rewritten}Segundo {source}."


def _is_generic_source_label(source: str) -> bool:
    normalized = source.lower().replace("-", " ").replace("_", " ").strip()
    generic_labels = {
        "economia brasil",
        "economia nacional",
        "economia global",
        "economia mundao",
        "politica brasil",
        "politica nacional",
        "politica mundao",
        "geopolitica",
        "criptoativos",
        "tech",
        "tecnologia",
    }
    return normalized in generic_labels


def _soften_group_tone(text: str) -> str:
    softened = text
    replacements = (
        ("player estrategico", "ator relevante"),
        ("player estratégico", "ator relevante"),
        ("se consolida como", "fica mais forte como"),
    )
    for source, target in replacements:
        softened = re.sub(source, target, softened, flags=re.IGNORECASE)
    return softened


def _limit_group_sentences(text: str, max_sentences: int = 2) -> str:
    parts = re.split(r"(?<=[.!?])\s+", text)
    sentences = [part.strip() for part in parts if part.strip()]
    if not sentences:
        return text
    return " ".join(sentences[:max_sentences]).strip()


async def _retrieve_context(question: str, max_items: int = 5) -> str:
    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=24)
    async with async_session() as session:
        result = await session.execute(
            select(Summary)
            .where(Summary.created_at >= cutoff)
            .order_by(Summary.created_at.desc())
        )
        summaries = result.scalars().all()

    if not summaries:
        return ""

    keywords = _extract_keywords(question)
    scored_summaries = []
    for summary in summaries:
        takeaways = normalize_takeaways(
            summary.key_takeaways,
            summary_text=summary.summary_text or "",
            category=summary.category,
            period=summary.period,
        )
        searchable_parts = [
            summary.summary_text or "",
            takeaways["header"],
            takeaways["insight"],
            *takeaways["bullets"],
            *[section["content"] for section in takeaways["sections"]],
        ]
        haystack = _normalized_text(" ".join(searchable_parts))
        score = sum(1 for keyword in keywords if keyword in haystack)
        scored_summaries.append((score, summary, takeaways))

    scored_summaries.sort(key=lambda item: item[0], reverse=True)
    top_items = [item for item in scored_summaries[:max_items] if item[0] > 0]
    if not top_items:
        top_items = scored_summaries[:3]

    context_parts = []
    article_ids: list[int] = []
    for _, summary, takeaways in top_items:
        context_parts.append(
            "\n".join(
                [
                    f"[{summary.category} - {summary.period}] {takeaways['header']}",
                    *(f"- {bullet}" for bullet in takeaways["bullets"]),
                    takeaways["insight"],
                    *[f"{section['title']}: {section['content']}" for section in takeaways["sections"]],
                ]
            ).strip()
        )
        raw_ids = summary.source_article_ids or []
        if isinstance(raw_ids, dict):
            raw_ids = raw_ids.get("ids", [])
        article_ids.extend([article_id for article_id in raw_ids if isinstance(article_id, int)])

    if article_ids:
        async with async_session() as session:
            result = await session.execute(
                select(NewsArticle)
                .options(joinedload(NewsArticle.source))
                .where(NewsArticle.id.in_(article_ids[:5]))
            )
            articles = result.scalars().all()

        for article in articles:
            excerpt = (article.raw_content or "")[:500]
            source_name = article.source.name if article.source else ""
            context_parts.append(f"[{article.title} - {source_name}]\n{excerpt}")

    return "\n\n---\n\n".join(part for part in context_parts if part)


def _normalized_text(text: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", text or "")
    ascii_text = "".join(char for char in ascii_text if not unicodedata.combining(char))
    return ascii_text.lower()


def _extract_keywords(text: str) -> list[str]:
    stop_words = {
        "o",
        "a",
        "os",
        "as",
        "de",
        "do",
        "da",
        "dos",
        "das",
        "em",
        "no",
        "na",
        "nos",
        "nas",
        "por",
        "para",
        "com",
        "um",
        "uma",
        "uns",
        "umas",
        "e",
        "ou",
        "mas",
        "que",
        "se",
        "como",
        "sobre",
        "entre",
        "foi",
        "eh",
        "esta",
        "sao",
        "ser",
        "ter",
        "pode",
        "qual",
        "quando",
        "onde",
        "quem",
        "quanto",
        "me",
        "te",
        "lhe",
        "nos",
        "vos",
        "lhes",
        "isso",
        "isto",
        "aquilo",
        "nao",
        "sim",
        "ja",
        "ainda",
        "so",
        "mais",
        "menos",
        "muito",
        "pouco",
        "tudo",
        "nada",
        "algo",
        "alguem",
        "ninguem",
        "todo",
        "cada",
        "outro",
    }

    cleaned = _normalized_text(text)
    words = cleaned.split()
    return [word.strip("?,!.:;") for word in words if word not in stop_words and len(word) > 2]


async def _retrieve_conversation_history(phone_number: str, max_messages: int = 5) -> str:
    async with async_session() as session:
        result = await session.execute(select(Subscriber).where(Subscriber.phone_number == phone_number))
        subscriber = result.scalar_one_or_none()
        if not subscriber:
            return ""

        result = await session.execute(
            select(UserInteraction)
            .where(
                UserInteraction.subscriber_id == subscriber.id,
                UserInteraction.message_type == "question",
            )
            .order_by(desc(UserInteraction.created_at))
            .limit(max_messages)
        )
        interactions = result.scalars().all()

    if not interactions:
        return ""

    interactions = list(reversed(interactions))
    parts: list[str] = []
    for interaction in interactions:
        parts.append(f"Usuário: {interaction.incoming_message}")
        if interaction.response_message:
            response = interaction.response_message[:300]
            if len(interaction.response_message) > 300:
                response += "..."
            parts.append(f"Assistente: {response}")
    return "\n".join(parts)
