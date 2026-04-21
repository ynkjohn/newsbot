import asyncio
import datetime

import structlog
from sqlalchemy import select

from db.engine import async_session
from db.models import Subscriber, UserInteraction
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
    """Process an incoming WhatsApp message.
    
    Args:
        remote_jid: The remote JID (user, group, or broadcast)
        body: The message text
    """
    # remote_jid can be either:
    # - User: 55149991749864@s.whatsapp.net or 55149991749864@lid
    # - Group: 120363040996567349@g.us
    
    # Detect if this is a group message
    is_group = remote_jid.endswith("@g.us")
    
    logger.info(f"Processing message from {remote_jid} (group={is_group}): {body[:30]}")
    
    try:
        # Parse the message with context
        message_type, detail = parse_message(body, is_group=is_group)
        logger.info(f"Parsed as {message_type}: {detail} (group={is_group})")

        phone_for_db = _normalize_remote_jid(remote_jid)

        # Route to appropriate handler
        response = None

        command_target = remote_jid if is_group else phone_for_db

        if message_type == "command":
            logger.info(f"Handling command: {detail}")
            response = await handle_command(detail, command_target)
            logger.info(f"Command response: {response[:100] if response else 'None'}...")
        elif message_type == "question":
            logger.info(f"Handling question: {body}")
            response = await handle_question(
                remote_jid if is_group else phone_for_db,
                body,
                is_group=is_group,
            )
            logger.info(f"Question response: {response[:100] if response else 'None'}...")
        else:
            # Ignore other message types (spam, random text, etc)
            logger.info(f"Ignoring message type: {message_type}")
            return

        logger.info(f"Response generated: {response[:50] if response else 'None'}...")

        # Log the interaction
        if not is_group:
            subscriber = await get_or_create_subscriber(phone_for_db, active=False)
            await _log_interaction(
                subscriber_id=subscriber.id,
                incoming=body,
                message_type=message_type,
                command=detail if message_type == "command" else None,
                response=response,
            )

        # Send response via WhatsApp (use full remote_jid for proper routing)
        if response:
            from delivery.whatsapp_sender import send_single_message

            if is_group and message_type == "question":
                remember_group_interaction(remote_jid, body, response)

            logger.info(f"Sending response to {remote_jid}")
            await send_single_message(remote_jid, response)
            logger.info(f"Response sent successfully")
    except Exception as e:
        logger.error(f"Error processing message: {e}", exc_info=True)


async def _log_interaction(
    subscriber_id: int,
    incoming: str,
    message_type: str,
    command: str | None,
    response: str | None,
) -> None:
    """Log user interaction to the database."""
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
