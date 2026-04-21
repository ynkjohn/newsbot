import asyncio
import datetime
import random
import re
import time

import structlog
import requests
from sqlalchemy.exc import SQLAlchemyError
from requests.exceptions import Timeout, ConnectionError, RequestException

from config.settings import settings
from db.engine import async_session
from db.models import DeliveryLog, Subscriber, Summary
from delivery.message_formatter import (
    filter_summaries_by_preferences,
    format_digest,
    format_afternoon_digest,
    format_morning_digest,
    split_message,
)
from delivery.rate_limiter import TokenBucketRateLimiter

logger = structlog.get_logger()

rate_limiter = TokenBucketRateLimiter(rate=settings.send_rate_limit)


def _format_phone(phone_number: str) -> str:
    """Format phone number for WhatsApp Bridge.
    
    Handles:
    - Regular WhatsApp (@s.whatsapp.net)
    - LID (Live ID) format (@lid)
    - Group JIDs (@g.us)
    """
    # Remove whatsapp: prefix if present
    phone = phone_number.replace("whatsapp:", "").strip()
    
    # If it's already a properly formatted JID, return as-is
    if "@g.us" in phone or "@lid" in phone or "@s.whatsapp.net" in phone:
        return phone
    
    # Otherwise format as regular WhatsApp user
    return f"{phone}@s.whatsapp.net"


def _send_whatsapp_message(phone_number: str, text: str) -> dict | None:
    """Send a WhatsApp message via the whatsapp-bridge microservice.
    
    Uses exponential backoff retry: 3 attempts with 1s → 5s → 10s delays.
    Differentiates between retryable (timeout, connection) and non-retryable (4xx) errors.
    
    Returns the API response dict on success, None on failure after all retries.
    """
    url = f"{settings.whatsapp_bridge_url}/send"
    payload = {
        "number": _format_phone(phone_number),
        "text": text,
    }
    headers = {}
    if settings.whatsapp_bridge_token:
        headers["Authorization"] = f"Bearer {settings.whatsapp_bridge_token}"

    max_retries = 3
    backoff_delays = [1, 5, 10]  # seconds
    
    for attempt in range(1, max_retries + 1):
        try:
            logger.debug(f"WhatsApp send attempt {attempt}/{max_retries} to {phone_number}")
            response = requests.post(url, json=payload, headers=headers, timeout=30)
            response.raise_for_status()
            return response.json()
            
        except Timeout as e:
            # Timeout - retryable
            if attempt < max_retries:
                wait_time = backoff_delays[attempt - 1]
                logger.warning(
                    f"WhatsApp timeout on attempt {attempt}/{max_retries} to {phone_number}, "
                    f"retrying in {wait_time}s"
                )
                time.sleep(wait_time)
                continue
            else:
                logger.error(f"WhatsApp timeout after {max_retries} attempts to {phone_number}")
                return None
                
        except ConnectionError as e:
            # Connection error - retryable
            if attempt < max_retries:
                wait_time = backoff_delays[attempt - 1]
                logger.warning(
                    f"WhatsApp connection error on attempt {attempt}/{max_retries} to {phone_number}, "
                    f"retrying in {wait_time}s: {e}"
                )
                time.sleep(wait_time)
                continue
            else:
                logger.error(f"WhatsApp connection error after {max_retries} attempts to {phone_number}")
                return None
                
        except requests.exceptions.HTTPError as e:
            # Check status code
            status_code = e.response.status_code if hasattr(e.response, 'status_code') else None
            
            # 4xx errors are not retryable
            if status_code and 400 <= status_code < 500:
                logger.error(f"WhatsApp Bridge client error {status_code} (non-retryable): {e}")
                return None
            
            # 5xx errors are retryable
            if status_code and 500 <= status_code < 600:
                if attempt < max_retries:
                    wait_time = backoff_delays[attempt - 1]
                    logger.warning(
                        f"WhatsApp server error {status_code} on attempt {attempt}/{max_retries}, "
                        f"retrying in {wait_time}s"
                    )
                    time.sleep(wait_time)
                    continue
                else:
                    logger.error(f"WhatsApp server error after {max_retries} attempts")
                    return None
            
            # Other HTTP errors
            logger.error(f"WhatsApp Bridge HTTP error: {e}")
            return None
            
        except RequestException as e:
            # Generic request exception
            logger.error(f"WhatsApp Bridge request error: {type(e).__name__}: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected WhatsApp error: {type(e).__name__}: {e}")
            return None
    
    return None


async def send_digest(subscribers: list[Subscriber], summaries: list[Summary], period: str) -> int:
    """Send digest to all active subscribers.
    
    Features:
    - Per-subscriber tracking with sent_count
    - Exponential backoff retry (1s → 5s → 10s) on failures
    - Only updates last_sent_at if at least one message succeeded
    - Logs one delivery result per summary/subscriber pair

    Returns count of subscribers who received at least one part.
    """
    if not summaries or not subscribers:
        return 0

    subscribers = _filter_delivery_subscribers(subscribers)
    subscribers = _deduplicate_subscribers(subscribers)
    if not subscribers:
        return 0

    date = datetime.date.today()
    formatter = lambda s, d: format_digest(s, d, period)

    sent_subscribers = 0
    delivered_summary_ids: set[int] = set()
    backoff_delays = [1, 5, 10]  # seconds for retry

    for subscriber in subscribers:
        filtered = filter_summaries_by_preferences(summaries, subscriber.preferences or {})
        if not filtered:
            continue

        full_text = formatter(filtered, date)
        parts = split_message(full_text)
        
        subscriber_sent = False  # Track if this subscriber got ANY message
        parts_succeeded = 0
        parts_failed = 0

        for part_idx, part in enumerate(parts):
            await rate_limiter.acquire()

            # Random jitter to avoid mechanical patterns
            await asyncio.sleep(random.uniform(0.05, 0.2))

            # Try to send with retries
            part_sent = False
            for attempt in range(1, 4):  # 3 attempts
                result = await asyncio.to_thread(
                    _send_whatsapp_message, subscriber.phone_number, part
                )

                if result:
                    logger.info(
                        f"WhatsApp message sent to {subscriber.phone_number} "
                        f"(part {part_idx + 1}/{len(parts)}, attempt {attempt})"
                    )
                    part_sent = True
                    parts_succeeded += 1
                    break
                else:
                    if attempt < 3:
                        wait_time = backoff_delays[attempt - 1]
                        logger.warning(
                            f"WhatsApp part {part_idx + 1} failed attempt {attempt}/3 to {subscriber.phone_number}, "
                            f"retrying in {wait_time}s"
                        )
                        await asyncio.sleep(wait_time)
                    else:
                        logger.error(
                            f"WhatsApp part {part_idx + 1} failed after 3 attempts to {subscriber.phone_number}"
                        )
                        parts_failed += 1

            if part_sent:
                subscriber_sent = True

            # Delay between parts of the same digest
            if part_idx < len(parts) - 1:
                await asyncio.sleep(3)

        # Update last_sent_at ONLY if subscriber got at least one message
        if subscriber_sent:
            delivered_summary_ids.update(summary.id for summary in filtered)
            try:
                async with async_session() as session:
                    sub = await session.get(Subscriber, subscriber.id)
                    if sub:
                        sub.last_sent_at = datetime.datetime.now(datetime.timezone.utc)
                        await session.commit()
                        logger.debug(f"Updated last_sent_at for subscriber {subscriber.id}")
            except SQLAlchemyError as e:
                logger.error(f"Failed to update last_sent_at for subscriber {subscriber.id}: {e}")
            await _log_delivery_results(subscriber.id, filtered, "sent")
        else:
            logger.warning(f"No parts sent to {subscriber.phone_number}, skipping last_sent_at update")
            if parts_failed > 0 and parts_succeeded == 0:
                await _log_delivery_results(
                    subscriber.id,
                    filtered,
                    "failed",
                    f"All {len(parts)} parts failed to deliver",
                )

        # Delay between different subscribers
        await asyncio.sleep(1)
        
        if subscriber_sent:
            sent_subscribers += 1

    if sent_subscribers > 0 and delivered_summary_ids:
        try:
            sent_at = datetime.datetime.now(datetime.timezone.utc)
            async with async_session() as session:
                for summary_id in delivered_summary_ids:
                    s = await session.get(Summary, summary_id)
                    if s:
                        s.sent_at = sent_at
                await session.commit()
                logger.info(f"Marked {len(delivered_summary_ids)} summaries as sent")
        except SQLAlchemyError as e:
            logger.error(f"Failed to mark summaries as sent: {e}")

    return sent_subscribers


async def send_single_message(phone_number: str, text: str) -> str | None:
    """Send a single WhatsApp message via whatsapp-bridge. Returns 'sent' or None on failure."""
    await rate_limiter.acquire()
    # Run blocking sync function in thread pool to avoid blocking event loop
    result = await asyncio.to_thread(_send_whatsapp_message, phone_number, text)

    if result:
        return "sent"

    logger.error(f"Failed to send message to {phone_number}")
    return None


async def _log_delivery(
    subscriber_id: int,
    summary_id: int,
    status: str,
    error_message: str | None = None,
) -> None:
    async with async_session() as session:
        log = DeliveryLog(
            subscriber_id=subscriber_id,
            summary_id=summary_id,
            status=status,
            error_message=error_message,
        )
        session.add(log)
        await session.commit()


async def _log_delivery_results(
    subscriber_id: int,
    summaries: list[Summary],
    status: str,
    error_message: str | None = None,
) -> None:
    for summary in summaries:
        await _log_delivery(subscriber_id, summary.id, status, error_message)


def _deduplicate_subscribers(subscribers: list[Subscriber]) -> list[Subscriber]:
    """Keep a single preferred subscriber entry per real WhatsApp destination.

    Legacy rows may store the same destination in plain numeric form while newer
    rows keep the explicit WhatsApp JID. We collapse those variants and prefer
    the most specific address format, including groups.
    """
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
    """Restrict digest delivery to the configured allowlist when present."""
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
