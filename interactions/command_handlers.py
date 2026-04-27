from sqlalchemy import select

from config.time_utils import local_today
from db.engine import async_session
from db.models import Summary
from interactions.drilldown_handler import build_drilldown_response_for_command
from interactions.messages import category_summary_label, help_text, no_summary_available
from interactions.subscriber_manager import subscribe, unsubscribe

HELP_TEXT = help_text()


async def handle_command(command: str, phone_number: str) -> str:
    from interactions.command_router import COMMANDS

    handler_type, detail = COMMANDS.get(command, (None, None))

    if handler_type is None:
        drilldown_response = await build_drilldown_response_for_command(command)
        if drilldown_response:
            return drilldown_response
        return HELP_TEXT

    if handler_type == "subscribe":
        return await subscribe(phone_number)
    if handler_type == "unsubscribe":
        return await unsubscribe(phone_number)
    if handler_type == "help":
        return HELP_TEXT
    if handler_type == "category":
        return await _get_category_summary(detail)
    if handler_type == "category_world":
        return await _get_world_summaries()
    if handler_type == "today":
        return await _get_today_summaries()
    return HELP_TEXT


async def _get_category_summary(category: str) -> str:
    async with async_session() as session:
        result = await session.execute(
            select(Summary)
            .where(Summary.category == category)
            .order_by(Summary.created_at.desc())
            .limit(1)
        )
        summary = result.scalar_one_or_none()

    if not summary:
        return no_summary_available(category_summary_label(category))
    return summary.summary_text


async def _get_world_summaries() -> str:
    async with async_session() as session:
        result = await session.execute(
            select(Summary)
            .where(Summary.category.in_(["economia-mundao", "politica-mundao"]))
            .order_by(Summary.created_at.desc())
            .limit(2)
        )
        summaries = result.scalars().all()

    if not summaries:
        return no_summary_available("Geopolítica e economia global")
    return "\n\n".join(summary.summary_text for summary in summaries)


async def _get_today_summaries() -> str:
    today = local_today()
    async with async_session() as session:
        result = await session.execute(
            select(Summary)
            .where(Summary.date == today)
            .order_by(Summary.period.asc(), Summary.category.asc())
        )
        summaries = result.scalars().all()

    if not summaries:
        return no_summary_available("o fechamento de hoje")
    return "\n\n".join(summary.summary_text for summary in summaries)
