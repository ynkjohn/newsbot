import datetime

from db.models import Summary
from interactions.messages import digest_footer, digest_intro
from processor.summary_format import normalize_takeaways, render_summary_text


def format_digest(summaries: list[Summary], date: datetime.date, period: str) -> str:
    parts = [digest_intro(period, date), ""]

    for summary in summaries:
        parts.append(format_summary_for_delivery(summary))
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

    blocks = text.split("\n\n")
    parts: list[str] = []
    current = ""

    for block in blocks:
        candidate = f"{current}\n\n{block}".strip() if current else block
        if len(candidate) > max_chars and current:
            parts.append(current.strip())
            current = block
        else:
            current = candidate

    if current.strip():
        parts.append(current.strip())

    if len(parts) > 1:
        total = len(parts)
        parts = [f"NewsBot {index + 1}/{total}\n\n{part}" for index, part in enumerate(parts)]

    return parts


def filter_summaries_by_preferences(summaries: list[Summary], preferences: dict) -> list[Summary]:
    if not preferences or "categories" not in preferences:
        return summaries

    preferred = set(preferences.get("categories", []))
    if not preferred:
        return summaries

    return [summary for summary in summaries if summary.category in preferred]
