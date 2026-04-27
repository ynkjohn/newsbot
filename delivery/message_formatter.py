import datetime

from db.models import Summary
from interactions.messages import digest_footer

try:
    from processor.news_taxonomy import SUMMARY_CATEGORIES, command_for_category
except ModuleNotFoundError:  # pragma: no cover - compatibility with Task 2 branches
    from processor.summary_format import command_for_category as _legacy_command_for_category

    SUMMARY_CATEGORIES = [
        "politica-brasil",
        "economia-brasil",
        "economia-cripto",
        "economia-mundao",
        "politica-mundao",
        "tech",
    ]

    def command_for_category(category: str) -> str:
        overrides = {
            "politica-brasil": "!politica",
            "economia-brasil": "!economia",
            "economia-cripto": "!cripto",
            "economia-mundao": "!mundao",
            "politica-mundao": "!geopolitica",
            "tech": "!tech",
        }
        return overrides.get(category, _legacy_command_for_category(category))
from processor.summary_format import normalize_takeaways, render_headline_item, render_summary_text, trusted_items


BULLETIN_LIMITS = {
    "politica-brasil": 3,
    "economia-brasil": 2,
    "economia-cripto": 2,
    "economia-mundao": 2,
    "politica-mundao": 2,
    "tech": 2,
}

BULLETIN_HEADERS = {
    "politica-brasil": "🇧🇷 Política Brasil",
    "economia-brasil": "💰 Economia Brasil",
    "economia-cripto": "₿ Cripto",
    "economia-mundao": "🌍 Economia Mundo",
    "politica-mundao": "🌍 Mundo",
    "tech": "🚀 Tech",
}


def format_digest(summaries: list[Summary], date: datetime.date, period: str) -> str:
    period_label = {
        "morning": "Manhã",
        "midday": "Meio-dia",
        "afternoon": "Tarde",
        "evening": "Noite",
    }.get(period, period or "Resumo")
    summaries_by_category = {summary.category: summary for summary in summaries}
    parts = [f"NewsBot — {period_label} {date.strftime('%d/%m')}", ""]
    next_number = 1

    for category in SUMMARY_CATEGORIES:
        header = BULLETIN_HEADERS.get(category, category)
        command = command_for_category(category)
        summary = summaries_by_category.get(category)
        parts.append(header)

        if summary is None:
            parts.append(f"Sem manchetes confiáveis agora. Veja a editoria em {command}.")
            parts.append("")
            continue

        takeaways = normalize_takeaways(
            summary.key_takeaways,
            summary_text=summary.summary_text or "",
            category=summary.category,
            period=summary.period,
        )
        items = trusted_items(takeaways)
        if not items:
            parts.append(f"Sem manchetes confiáveis agora. Veja a editoria em {command}.")
            parts.append("")
            continue

        limit = BULLETIN_LIMITS.get(category, 2)
        visible = items[:limit]
        for item in visible:
            parts.append(render_headline_item(next_number, item))
            next_number += 1

        remaining = len(items) - len(visible)
        if remaining > 0:
            parts.append(f"+{remaining} em {command}")
        parts.append("")

    parts.append(digest_footer())
    return "\n".join(part for part in parts if part is not None).strip()


def format_summary_for_delivery(summary: Summary) -> str:
    takeaways = normalize_takeaways(
        summary.key_takeaways,
        summary_text=summary.summary_text or "",
        category=summary.category,
        period=summary.period,
    )
    return render_summary_text(summary.category, summary.period, takeaways)


def split_message(text: str, max_chars: int = 4000) -> list[str]:
    if len(text) <= max_chars:
        return [text]

    parts: list[str] = []
    current = ""

    for block in text.split("\n\n"):
        chunks = [block[index : index + max_chars] for index in range(0, len(block), max_chars)] or [""]
        for chunk in chunks:
            candidate = f"{current}\n\n{chunk}".strip() if current else chunk
            if len(candidate) > max_chars and current:
                parts.append(current.strip())
                current = chunk
            else:
                current = candidate

    if current.strip():
        parts.append(current.strip())

    return parts


def filter_summaries_by_preferences(summaries: list[Summary], preferences: dict) -> list[Summary]:
    if not preferences or "categories" not in preferences:
        return summaries

    preferred = set(preferences.get("categories", []))
    if not preferred:
        return summaries

    return [summary for summary in summaries if summary.category in preferred]
