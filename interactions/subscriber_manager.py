import structlog
from sqlalchemy import select

from config.settings import settings
from db.engine import async_session
from db.models import Subscriber
from interactions.messages import (
    already_subscribed,
    not_subscribed,
    subscribe_confirmation,
    subscribe_reactivated,
    unsubscribe_confirmation,
)

logger = structlog.get_logger()


async def get_or_create_subscriber(
    phone_number: str,
    name: str | None = None,
    active: bool = False,
) -> Subscriber:
    """Get existing subscriber or create a new one."""
    async with async_session() as session:
        result = await session.execute(
            select(Subscriber).where(Subscriber.phone_number == phone_number)
        )
        subscriber = result.scalar_one_or_none()

        if subscriber:
            return subscriber

        subscriber = Subscriber(phone_number=phone_number, name=name, active=active)
        session.add(subscriber)
        await session.commit()
        await session.refresh(subscriber)
        logger.info(f"New subscriber: {phone_number}")
        return subscriber


async def subscribe(phone_number: str) -> str:
    """Subscribe a phone number. Returns confirmation message."""
    async with async_session() as session:
        result = await session.execute(
            select(Subscriber).where(Subscriber.phone_number == phone_number)
        )
        subscriber = result.scalar_one_or_none()

        sched = settings.pipeline_schedule_display_br

        if subscriber and subscriber.active:
            return already_subscribed(sched)

        if subscriber:
            subscriber.active = True
            await session.commit()
            return subscribe_reactivated(sched)

        subscriber = Subscriber(phone_number=phone_number, active=True)
        session.add(subscriber)
        await session.commit()
        logger.info(f"New subscription: {phone_number}")
        return subscribe_confirmation(sched)


async def unsubscribe(phone_number: str) -> str:
    """Unsubscribe a phone number. Returns confirmation message."""
    async with async_session() as session:
        result = await session.execute(
            select(Subscriber).where(Subscriber.phone_number == phone_number)
        )
        subscriber = result.scalar_one_or_none()

        if not subscriber or not subscriber.active:
            return not_subscribed()

        subscriber.active = False
        await session.commit()
        logger.info(f"Unsubscribed: {phone_number}")
        return unsubscribe_confirmation()


async def is_subscribed(phone_number: str) -> bool:
    """Check if a phone number is an active subscriber."""
    async with async_session() as session:
        result = await session.execute(
            select(Subscriber).where(
                Subscriber.phone_number == phone_number,
                Subscriber.active == True,  # noqa: E712
            )
        )
        return result.scalar_one_or_none() is not None
