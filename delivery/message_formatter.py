import datetime

from db.models import Summary


PERIOD_LABELS = {
    "morning":   "Manhã",
    "midday":    "Meio-dia",
    "afternoon": "Tarde",
    "evening":   "Noite",
}

PERIOD_GREETINGS = {
    "morning":   "Bom dia",
    "midday":    "Bom dia",
    "afternoon": "Boa tarde",
    "evening":   "Boa noite",
}


def format_digest(summaries: list[Summary], date: datetime.date, period: str) -> str:
    """Format digest message for any period."""
    greeting = PERIOD_GREETINGS.get(period, "Olá")
    label = PERIOD_LABELS.get(period, period.capitalize())
    parts = [f"{greeting}! Resumo de notícias ({label}) — {date.strftime('%d/%m/%Y')}", ""]

    for summary in summaries:
        parts.append(summary.summary_text)
        parts.append("")

    parts.append("Comandos: !politica !economia !cripto !geopolitica !tech")
    parts.append("Você também pode perguntar sobre qualquer notícia do dia!")

    return "\n".join(parts)


def format_morning_digest(summaries: list[Summary], date: datetime.date) -> str:
    return format_digest(summaries, date, "morning")


def format_afternoon_digest(summaries: list[Summary], date: datetime.date) -> str:
    return format_digest(summaries, date, "afternoon")


def split_message(text: str, max_chars: int = 4000) -> list[str]:
    """Split a long message into parts that fit within WhatsApp practical limits.

    Splits on double newlines (category boundaries) when possible.
    """
    if len(text) <= max_chars:
        return [text]

    # Split by category blocks (double newline)
    blocks = text.split("\n\n")
    parts: list[str] = []
    current = ""

    for block in blocks:
        if len(current) + len(block) + 2 > max_chars:
            if current:
                parts.append(current.strip())
            current = block
        else:
            current = current + "\n\n" + block if current else block

    if current.strip():
        parts.append(current.strip())

    # Add part numbering
    if len(parts) > 1:
        total = len(parts)
        parts = [f"Noticias - {i+1}/{total}\n\n{p}" for i, p in enumerate(parts)]

    return parts


def filter_summaries_by_preferences(
    summaries: list[Summary], preferences: dict
) -> list[Summary]:
    """Filter summaries based on subscriber preferences.

    If preferences is empty or has no categories, return all summaries.
    """
    if not preferences or "categories" not in preferences:
        return summaries

    preferred = set(preferences.get("categories", []))
    if not preferred:
        return summaries

    return [s for s in summaries if s.category in preferred]
