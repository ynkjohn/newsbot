import datetime

import structlog
from sqlalchemy import select

from db.engine import async_session
from db.models import Subscriber, Summary
from interactions.subscriber_manager import subscribe, unsubscribe

logger = structlog.get_logger()

HELP_TEXT = """Comandos disponíveis:

!start ou !inscrever — Inscrever-se nos resumos
!stop ou !sair — Cancelar inscrição
!politica — Política Nacional
!economia — Economia Nacional
!cripto — Criptoativos
!geopolitica — Geopolítica e Economia Global
!tech — Tecnologia
!hoje — Todos os resumos do dia

Você também pode perguntar sobre qualquer notícia do dia!"""


async def handle_command(command: str, phone_number: str) -> str:
    """Handle a parsed command. Returns the response text."""
    from interactions.command_router import COMMANDS

    handler_type, detail = COMMANDS.get(command, ("help", "Lista de comandos"))

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
    """Get the latest summary for a specific category."""
    async with async_session() as session:
        result = await session.execute(
            select(Summary)
            .where(Summary.category == category)
            .order_by(Summary.created_at.desc())
            .limit(1)
        )
        summary = result.scalar_one_or_none()

    if not summary:
        return f"Nenhum resumo disponivel para {category} ainda."

    return summary.summary_text


async def _get_world_summaries() -> str:
    """Get latest summaries for economia-mundao + politica-mundao."""
    async with async_session() as session:
        result = await session.execute(
            select(Summary)
            .where(Summary.category.in_(["economia-mundao", "politica-mundao"]))
            .order_by(Summary.created_at.desc())
            .limit(2)
        )
        summaries = result.scalars().all()

    if not summaries:
        return "Nenhum resumo disponivel para mundo ainda."

    return "\n\n".join(s.summary_text for s in summaries)


async def _get_today_summaries() -> str:
    """Get all summaries from today."""
    today = datetime.date.today()
    async with async_session() as session:
        result = await session.execute(
            select(Summary)
            .where(Summary.date == today)
            .order_by(Summary.category)
        )
        summaries = result.scalars().all()

    if not summaries:
        return "Nenhum resumo disponivel para hoje ainda."

    return "\n\n".join(s.summary_text for s in summaries)
