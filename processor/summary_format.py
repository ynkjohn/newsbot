from __future__ import annotations

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

SUMMARY_SCHEMA_VERSION = 2


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
        if not sections:
            sections = _derive_sections_from_legacy(summary_text, header, insight)

        normalized = {
            "version": int(raw_takeaways.get("version") or SUMMARY_SCHEMA_VERSION),
            "header": str(raw_takeaways.get("header") or header).strip(),
            "bullets": bullets,
            "insight": insight,
            "sections": sections,
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
    }


def build_takeaways_payload(
    *,
    header: str,
    bullets: list[str],
    insight: str,
    sections: list[dict[str, str]],
) -> dict[str, Any]:
    return {
        "version": SUMMARY_SCHEMA_VERSION,
        "header": header.strip(),
        "bullets": [bullet.strip() for bullet in bullets if bullet and bullet.strip()],
        "insight": insight.strip(),
        "sections": _clean_sections(sections),
    }


def render_summary_text(category: str, period: str, takeaways: dict[str, Any]) -> str:
    normalized = normalize_takeaways(takeaways, category=category, period=period)
    parts = [normalized["header"]]

    if normalized["bullets"]:
        parts.extend(
            [
                "",
                "Panorama",
                *[f"• {bullet}" for bullet in normalized["bullets"]],
            ]
        )

    if normalized["insight"]:
        parts.extend(["", "Insight-chave", normalized["insight"]])

    for section in normalized["sections"]:
        parts.extend(["", section["title"], section["content"]])

    return "\n".join(parts).strip()
