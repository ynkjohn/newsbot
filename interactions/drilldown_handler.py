from typing import Any

from sqlalchemy import select

from db.engine import async_session
from db.models import Summary


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


def _render_item_drilldown(item: dict[str, Any]) -> str | None:
    title = _text_value(item, "title")
    what_happened = _text_value(item, "what_happened")
    why_it_matters = _text_value(item, "why_it_matters")
    watchlist = _text_value(item, "watchlist")

    lead = what_happened or why_it_matters or watchlist
    if not lead:
        return f"*{title}*" if title else None

    parts: list[str] = []
    if title:
        parts.append(f"*{title}*\n{lead}")
    else:
        parts.append(lead)

    if why_it_matters and why_it_matters != lead:
        parts.append(why_it_matters)
    if watchlist and watchlist != lead:
        parts.append(f"O próximo ponto a observar: {watchlist}")

    return "\n\n".join(parts)


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

    return _render_item_drilldown(matches[0])
