import structlog

from db.engine import async_session
from db.models import UserInteraction
from interactions.command_handlers import handle_command
from interactions.command_router import parse_message
from interactions.question_handler import handle_question, remember_group_interaction
from interactions.subscriber_manager import get_or_create_subscriber

logger = structlog.get_logger()


def _normalize_remote_jid(remote_jid: str) -> str:
    return (
        remote_jid.replace("@s.whatsapp.net", "")
        .replace("@lid", "")
        .replace("@g.us", "")
        .strip()
    )


async def handle_incoming_message(remote_jid: str, body: str) -> None:
    is_group = remote_jid.endswith("@g.us")
    logger.info(f"Processing message from {remote_jid} (group={is_group}): {body[:30]}")

    try:
        message_type, detail = parse_message(body, is_group=is_group)
        phone_for_db = _normalize_remote_jid(remote_jid)
        response = None
        command_target = remote_jid if is_group else phone_for_db

        if message_type == "command":
            response = await handle_command(detail, command_target)
        elif message_type == "question":
            response = await handle_question(
                remote_jid if is_group else phone_for_db,
                body,
                is_group=is_group,
            )
        else:
            logger.info(f"Ignoring message type: {message_type}")
            return

        if not is_group:
            subscriber = await get_or_create_subscriber(phone_for_db, active=False)
            await _log_interaction(
                subscriber_id=subscriber.id,
                incoming=body,
                message_type=message_type,
                command=detail if message_type == "command" else None,
                response=response,
            )

        if response:
            from delivery.whatsapp_sender import send_single_message

            if is_group and message_type == "question":
                remember_group_interaction(remote_jid, body, response)

            await send_single_message(remote_jid, response)
            logger.info("Response sent successfully")
    except Exception as exc:
        logger.error(f"Error processing message: {exc}", exc_info=True)


async def _log_interaction(
    subscriber_id: int,
    incoming: str,
    message_type: str,
    command: str | None,
    response: str | None,
) -> None:
    async with async_session() as session:
        interaction = UserInteraction(
            subscriber_id=subscriber_id,
            incoming_message=incoming,
            message_type=message_type,
            command=command,
            response_message=response,
        )
        session.add(interaction)
        await session.commit()
