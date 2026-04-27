from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Any


CATEGORY_LABELS = {
    "politica-brasil": "Política Nacional",
    "economia-brasil": "Economia Nacional",
    "economia-cripto": "Criptoativos",
    "economia-mundao": "Economia Global",
    "politica-mundao": "Geopolítica",
    "tech": "Tecnologia",
}

CATEGORY_EMOJIS = {
    "politica-brasil": "🏛️",
    "economia-brasil": "💵",
    "economia-cripto": "₿",
    "economia-mundao": "🌍",
    "politica-mundao": "🧭",
    "tech": "🧠",
}

PERIOD_LABELS = {
    "morning": "Manhã",
    "midday": "Meio-dia",
    "afternoon": "Tarde",
    "evening": "Noite",
}

PERIOD_GREETINGS = {
    "morning": "Bom dia",
    "midday": "Bom dia",
    "afternoon": "Boa tarde",
    "evening": "Boa noite",
}

SECTION_TITLES = {
    "o_que_mudou": "O que mudou",
    "por_que_importa": "Por que importa",
    "watchlist": "Watchlist",
}

SUMMARY_SCHEMA_VERSION = 3

RESERVED_COMMANDS = {
    "!start",
    "!inscrever",
    "!stop",
    "!sair",
    "!politica",
    "!econbr",
    "!economia",
    "!geopolitica",
    "!mundao",
    "!econmundo",
    "!tech",
    "!cripto",
    "!hoje",
    "!explica",
    "!config",
    "!help",
}

COMMAND_STOPWORDS = {
    "a", "as", "ao", "aos", "de", "da", "das", "do", "dos", "e", "em", "no", "nos", "na", "nas", "o", "os", "para", "por", "com", "sem", "novo", "nova", "alta", "queda", "sobre", "registra", "libera", "reduz", "violacoes", "violaoes",
}

CATEGORY_HEADLINE_LIMIT = 6

CATEGORY_TITLES = {
    "politica-brasil": "🏛️ POLÍTICA BRASIL",
    "economia-brasil": "📈 ECONOMIA",
    "economia-cripto": "🌐 CRIPTO",
    "economia-mundao": "🌍 ECONOMIA GLOBAL",
    "politica-mundao": "🌍 GEOPOLÍTICA",
    "tech": "🚀 TECNOLOGIA",
    "cripto-tech": "🚀 CRIPTO & TECH",
}

CATEGORY_COMMANDS = {
    "politica-brasil": "!politica",
    "economia-brasil": "!economia",
    "economia-cripto": "!cripto",
    "economia-mundao": "!mundao",
    "politica-mundao": "!geopolitica",
    "tech": "!tech",
    "cripto-tech": "!tech",
}

SUMMARY_STATUS_PLACEHOLDER = "placeholder"
PLACEHOLDER_TEXT = "Resumo em preparação. Volte em instantes."

DIGEST_ITEM_TRUST_TRUSTED = "trusted"


def material_hash_for_item(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def slugify(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value or "")
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_value).strip("-")
    return re.sub(r"-+", "-", slug) or "item"


def command_hint_for_title(
    title: str,
    *,
    reserved_commands: set[str] | None = None,
    used_commands: set[str] | None = None,
) -> str:
    reserved = reserved_commands or RESERVED_COMMANDS
    used = used_commands or set()
    slug = slugify(title)
    terms = [term for term in slug.split("-") if term and term not in COMMAND_STOPWORDS]
    if not terms:
        terms = ["noticia"]

    priority_terms = [
        term
        for term in terms
        if term in {"pis", "pasep", "rav4", "gaza"} or any(char.isdigit() for char in term)
    ]
    primary = priority_terms[0] if priority_terms else terms[0]
    context_terms = [term for term in terms if term != primary]

    candidates: list[str] = [f"!{primary}"]
    if context_terms:
        candidates.append(f"!{primary}-{context_terms[0]}")
    if len(context_terms) >= 2:
        candidates.append(f"!{primary}-{context_terms[1]}")

    for candidate in candidates:
        if candidate not in reserved and candidate not in used:
            return candidate[:50]

    base = candidates[-1]
    for suffix in range(2, 100):
        candidate = f"{base}{suffix}"
        if candidate not in reserved and candidate not in used:
            return candidate[:50]
    return f"!{terms[0]}-{material_hash_for_item(title)[:6]}"[:50]


def ensure_item_commands(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    used: set[str] = set()
    normalized_items: list[dict[str, Any]] = []
    for item in items:
        updated = dict(item)
        command = str(updated.get("command_hint") or "").strip().lower()
        if (
            not command.startswith("!")
            or command in RESERVED_COMMANDS
            or command in used
            or " " in command
            or command[1:].isdigit()
        ):
            command = command_hint_for_title(
                str(updated.get("title") or updated.get("event_key") or "noticia"),
                reserved_commands=RESERVED_COMMANDS,
                used_commands=used,
            )
        used.add(command)
        updated["command_hint"] = command
        normalized_items.append(updated)
    return normalized_items


def trusted_items(takeaways: dict[str, Any]) -> list[dict[str, Any]]:
    items = [
        item
        for item in takeaways.get("items", [])
        if str(item.get("trust_status") or DIGEST_ITEM_TRUST_TRUSTED).lower() == DIGEST_ITEM_TRUST_TRUSTED
    ]
    items.sort(key=lambda item: (int(item.get("importance_score") or 3), -int(item.get("position") or 0)), reverse=True)
    return ensure_item_commands(items)


def render_headline_item(number: int, item: dict[str, Any]) -> str:
    title = " ".join(str(item.get("title") or "Noticia sem titulo").split()).strip()
    command = str(item.get("command_hint") or "").strip()
    return f"{number}. {title} — {command}".strip()


def command_for_category(category: str) -> str:
    return CATEGORY_COMMANDS.get(category, "!hoje")


def render_tech_teaser(takeaways: dict[str, Any]) -> str:
    insight = str(takeaways.get("insight") or "").strip()
    if insight:
        return insight
    bullets = takeaways.get("bullets") or []
    if bullets:
        return str(bullets[0]).strip()
    return "Sem manchetes confiáveis para esta editoria agora."


def render_legacy_summary(normalized: dict[str, Any], title: str) -> str:
    parts = [title]

    if normalized["bullets"]:
        parts.extend(
            [
                "",
                "Panorama",
                *[f"• {bullet}" for bullet in normalized["bullets"]],
            ]
        )

    if normalized["insight"]:
        parts.extend(["", normalized["insight"]])

    for section in normalized["sections"]:
        parts.extend(["", section["title"], section["content"]])

    return "\n".join(parts).strip()


def render_category_headlines(
    category: str,
    period: str,
    takeaways: dict[str, Any],
    *,
    start_number: int = 1,
    limit: int = CATEGORY_HEADLINE_LIMIT,
) -> str:
    normalized = normalize_takeaways(takeaways, category=category, period=period)
    title = CATEGORY_TITLES.get(category, normalized["header"])
    parts = [title]

    if normalized.get("status") == SUMMARY_STATUS_PLACEHOLDER:
        parts.extend(["", PLACEHOLDER_TEXT])
        return "\n".join(parts).strip()

    items = trusted_items(normalized)
    if not items:
        if category == "cripto-tech" and not any(
            [normalized["bullets"], normalized["insight"], normalized["sections"]]
        ):
            parts.extend(["", render_tech_teaser(normalized)])
            return "\n".join(parts).strip()
        return render_legacy_summary(normalized, title)

    visible = items[:limit]
    for offset, item in enumerate(visible):
        parts.append(render_headline_item(start_number + offset, item))

    remaining = max(0, len(items) - len(visible))
    if remaining:
        command = command_for_category(category)
        label = title.replace("🏛️", "").replace("📈", "").replace("🌍", "").replace("🌐", "").replace("🚀", "").strip().lower()
        parts.append(f"+{remaining} noticias em outro boletim de {label}.")
        parts.append(f"Para ver mais: {command}")

    parts.extend(["", "Para aprofundar, mande o comando da notícia."])
    return "\n".join(parts).strip()


def display_category(category: str) -> str:
    return CATEGORY_LABELS.get(category, category or "Categoria")


def display_period(period: str) -> str:
    return PERIOD_LABELS.get(period, period or "Janela")


def build_summary_header(category: str, period: str, header: str | None = None) -> str:
    if header and header.strip():
        return header.strip()
    emoji = CATEGORY_EMOJIS.get(category, "🗞️")
    return f"{emoji} {display_category(category)} — {display_period(period)}"


def extract_header(summary_text: str, fallback: str) -> str:
    lines = [line.strip() for line in (summary_text or "").splitlines() if line.strip()]
    return lines[0] if lines else fallback


def summary_paragraphs(summary_text: str, header: str) -> list[str]:
    text = (summary_text or "").strip()
    if not text:
        return []

    lines = [line.rstrip() for line in text.splitlines()]
    while lines and not lines[0].strip():
        lines.pop(0)

    if lines and lines[0].strip() == header.strip():
        lines.pop(0)

    body = "\n".join(lines).strip()
    return [paragraph.strip() for paragraph in body.split("\n\n") if paragraph.strip()]


def _clean_bullets(raw_bullets: Any) -> list[str]:
    if not isinstance(raw_bullets, list):
        return []
    cleaned: list[str] = []
    for item in raw_bullets:
        text = " ".join(str(item or "").split()).strip()
        if text:
            cleaned.append(text)
    return cleaned


def _clean_sections(raw_sections: Any) -> list[dict[str, str]]:
    if not isinstance(raw_sections, list):
        return []

    cleaned: list[dict[str, str]] = []
    for item in raw_sections:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").strip() or "contexto"
        title = str(item.get("title") or SECTION_TITLES.get(key) or key).strip()
        content = str(item.get("content") or "").strip()
        if content:
            cleaned.append({"key": key, "title": title, "content": content})
    return cleaned


def _derive_sections_from_legacy(summary_text: str, header: str, insight: str) -> list[dict[str, str]]:
    paragraphs = summary_paragraphs(summary_text, header)
    keys = ["o_que_mudou", "por_que_importa", "watchlist"]
    sections: list[dict[str, str]] = []
    for key, paragraph in zip(keys, paragraphs):
        sections.append({"key": key, "title": SECTION_TITLES[key], "content": paragraph})

    if not sections and insight:
        sections.append(
            {"key": "por_que_importa", "title": SECTION_TITLES["por_que_importa"], "content": insight}
        )
    return sections


def normalize_takeaways(
    raw_takeaways: Any,
    *,
    summary_text: str = "",
    category: str = "",
    period: str = "",
) -> dict[str, Any]:
    header = extract_header(summary_text, build_summary_header(category, period))

    if isinstance(raw_takeaways, dict):
        bullets = _clean_bullets(raw_takeaways.get("bullets"))
        insight = str(raw_takeaways.get("insight") or "").strip()
        sections = _clean_sections(raw_takeaways.get("sections"))
        items = [item for item in raw_takeaways.get("items", []) if isinstance(item, dict)]
        if not sections and not items:
            sections = _derive_sections_from_legacy(summary_text, header, insight)

        normalized = {
            "version": int(raw_takeaways.get("version") or SUMMARY_SCHEMA_VERSION),
            "header": str(raw_takeaways.get("header") or header).strip(),
            "bullets": bullets,
            "insight": insight,
            "sections": sections,
            "items": items,
            "status": str(raw_takeaways.get("status") or "").strip(),
        }
        return normalized

    bullets = _clean_bullets(raw_takeaways if isinstance(raw_takeaways, list) else [])
    sections = _derive_sections_from_legacy(summary_text, header, "")
    return {
        "version": 1,
        "header": header,
        "bullets": bullets,
        "insight": "",
        "sections": sections,
        "items": [],
        "status": "",
    }


def build_takeaways_payload(
    *,
    header: str,
    bullets: list[str],
    insight: str,
    sections: list[dict[str, str]],
    items: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "version": SUMMARY_SCHEMA_VERSION,
        "header": header.strip(),
        "bullets": [bullet.strip() for bullet in bullets if bullet and bullet.strip()],
        "insight": insight.strip(),
        "sections": _clean_sections(sections),
        "items": ensure_item_commands([item for item in items or [] if isinstance(item, dict)]),
    }


def render_summary_text(
    category: str,
    period: str,
    takeaways: dict[str, Any],
    *,
    teaser_only: bool = False,
) -> str:
    normalized = normalize_takeaways(takeaways, category=category, period=period)
    title = CATEGORY_TITLES.get(category, normalized["header"])

    if teaser_only:
        if trusted_items(normalized):
            return render_category_headlines(category, period, normalized, limit=2)
        return "\n".join([title, "", render_tech_teaser(normalized)]).strip()

    return render_category_headlines(category, period, normalized)
