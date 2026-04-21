import asyncio
import random
import re
import time

import requests
import structlog
from requests.exceptions import ConnectionError, RequestException, Timeout
from sqlalchemy.exc import SQLAlchemyError

from config.settings import settings
from config.time_utils import local_today, utc_now
from db.engine import async_session
from db.models import DeliveryLog, Subscriber, Summary
from delivery.message_formatter import filter_summaries_by_preferences, format_digest, split_message
from delivery.rate_limiter import TokenBucketRateLimiter

logger = structlog.get_logger()

rate_limiter = TokenBucketRateLimiter(rate=settings.send_rate_limit)


def _format_phone(phone_number: str) -> str:
    phone = phone_number.replace("whatsapp:", "").strip()
    if "@g.us" in phone or "@lid" in phone or "@s.whatsapp.net" in phone:
        return phone
    return f"{phone}@s.whatsapp.net"


def _send_whatsapp_message(phone_number: str, text: str) -> dict | None:
    url = f"{settings.whatsapp_bridge_url}/send"
    payload = {"number": _format_phone(phone_number), "text": text}
    headers = {}
    if settings.whatsapp_bridge_token:
        headers["Authorization"] = f"Bearer {settings.whatsapp_bridge_token}"

    max_retries = 3
    backoff_delays = [1, 5, 10]

    for attempt in range(1, max_retries + 1):
        try:
            response = requests.post(url, json=payload, headers=headers, timeout=30)
            response.raise_for_status()
            return response.json()
        except Timeout:
            if attempt < max_retries:
                time.sleep(backoff_delays[attempt - 1])
                continue
            logger.error(f"WhatsApp timeout after {max_retries} attempts to {phone_number}")
            return None
        except ConnectionError as exc:
            if attempt < max_retries:
                logger.warning(
                    f"WhatsApp connection error on attempt {attempt}/{max_retries} to {phone_number}: {exc}"
                )
                time.sleep(backoff_delays[attempt - 1])
                continue
            logger.error(f"WhatsApp connection error after {max_retries} attempts to {phone_number}")
            return None
        except requests.exceptions.HTTPError as exc:
            status_code = exc.response.status_code if hasattr(exc.response, "status_code") else None
            if status_code and 400 <= status_code < 500:
                logger.error(f"WhatsApp Bridge client error {status_code}: {exc}")
                return None
            if status_code and 500 <= status_code < 600 and attempt < max_retries:
                time.sleep(backoff_delays[attempt - 1])
                continue
            logger.error(f"WhatsApp Bridge HTTP error: {exc}")
            return None
        except RequestException as exc:
            logger.error(f"WhatsApp Bridge request error: {type(exc).__name__}: {exc}")
            return None
        except Exception as exc:
            logger.error(f"Unexpected WhatsApp error: {type(exc).__name__}: {exc}")
            return None

    return None


async def send_digest(subscribers: list[Subscriber], summaries: list[Summary], period: str) -> int:
    if not summaries or not subscribers:
        return 0

    subscribers = _filter_delivery_subscribers(subscribers)
    subscribers = _deduplicate_subscribers(subscribers)
    if not subscribers:
        return 0

    logical_date = local_today()
    sent_subscribers = 0
    delivered_summary_ids: set[int] = set()
    backoff_delays = [1, 5, 10]

    for subscriber in subscribers:
        filtered = filter_summaries_by_preferences(summaries, subscriber.preferences or {})
        if not filtered:
            continue

        full_text = format_digest(filtered, logical_date, period)
        parts = split_message(full_text)

        subscriber_sent = False
        parts_failed = 0

        for part_idx, part in enumerate(parts):
            await rate_limiter.acquire()
            await asyncio.sleep(random.uniform(0.05, 0.2))

            part_sent = False
            for attempt in range(1, 4):
                result = await asyncio.to_thread(_send_whatsapp_message, subscriber.phone_number, part)
                if result:
                    part_sent = True
                    subscriber_sent = True
                    break
                if attempt < 3:
                    await asyncio.sleep(backoff_delays[attempt - 1])

            if not part_sent:
                logger.error(
                    f"WhatsApp part {part_idx + 1}/{len(parts)} failed after retries to {subscriber.phone_number}"
                )
                parts_failed += 1

            if part_idx < len(parts) - 1:
                await asyncio.sleep(3)

        if subscriber_sent:
            delivered_summary_ids.update(summary.id for summary in filtered)
            await _mark_subscriber_sent(subscriber.id)
            await _log_delivery_results(subscriber.id, filtered, "sent")
            sent_subscribers += 1
        elif parts_failed:
            await _log_delivery_results(
                subscriber.id,
                filtered,
                "failed",
                f"All {len(parts)} parts failed to deliver",
            )

        await asyncio.sleep(1)

    if sent_subscribers > 0 and delivered_summary_ids:
        await _mark_summaries_sent(delivered_summary_ids)

    return sent_subscribers


async def send_single_message(phone_number: str, text: str) -> str | None:
    await rate_limiter.acquire()
    result = await asyncio.to_thread(_send_whatsapp_message, phone_number, text)
    if result:
        return "sent"
    logger.error(f"Failed to send message to {phone_number}")
    return None


async def _mark_subscriber_sent(subscriber_id: int) -> None:
    try:
        async with async_session() as session:
            subscriber = await session.get(Subscriber, subscriber_id)
            if subscriber:
                subscriber.last_sent_at = utc_now()
                await session.commit()
    except SQLAlchemyError as exc:
        logger.error(f"Failed to update last_sent_at for subscriber {subscriber_id}: {exc}")


async def _mark_summaries_sent(summary_ids: set[int]) -> None:
    try:
        sent_at = utc_now()
        async with async_session() as session:
            for summary_id in summary_ids:
                summary = await session.get(Summary, summary_id)
                if summary:
                    summary.sent_at = sent_at
            await session.commit()
    except SQLAlchemyError as exc:
        logger.error(f"Failed to mark summaries as sent: {exc}")


async def _log_delivery_results(
    subscriber_id: int,
    summaries: list[Summary],
    status: str,
    error_message: str | None = None,
) -> None:
    async with async_session() as session:
        for summary in summaries:
            session.add(
                DeliveryLog(
                    subscriber_id=subscriber_id,
                    summary_id=summary.id,
                    status=status,
                    error_message=error_message,
                )
            )
        await session.commit()


def _deduplicate_subscribers(subscribers: list[Subscriber]) -> list[Subscriber]:
    selected: dict[str, Subscriber] = {}
    for subscriber in subscribers:
        phone = (subscriber.phone_number or "").strip()
        if not phone:
            continue

        key = _subscriber_destination_key(phone)
        current = selected.get(key)
        if current is None or _subscriber_priority(phone) > _subscriber_priority(current.phone_number):
            selected[key] = subscriber
    return list(selected.values())


def _filter_delivery_subscribers(subscribers: list[Subscriber]) -> list[Subscriber]:
    if not settings.allowed_numbers:
        return subscribers

    allowed_list = [item.strip() for item in settings.allowed_numbers.split(",") if item.strip()]
    allowed_keys = {_subscriber_destination_key(item) for item in allowed_list}
    allowed_exact = {item for item in allowed_list if "@" in item}

    filtered: list[Subscriber] = []
    for subscriber in subscribers:
        phone = (subscriber.phone_number or "").strip()
        if not phone:
            continue

        if phone in allowed_exact or _subscriber_destination_key(phone) in allowed_keys:
            filtered.append(subscriber)
        else:
            logger.info(f"Skipping non-whitelisted subscriber in delivery: {phone}")
    return filtered


def _subscriber_destination_key(phone_number: str) -> str:
    digits = re.sub(r"\D", "", phone_number)
    return digits or phone_number


def _subscriber_priority(phone_number: str) -> int:
    if "@g.us" in phone_number:
        return 4
    if "@lid" in phone_number:
        return 3
    if "@s.whatsapp.net" in phone_number:
        return 2
    return 1
