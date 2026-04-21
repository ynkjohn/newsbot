from __future__ import annotations

from datetime import date, datetime, time, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from config.settings import settings


@lru_cache(maxsize=1)
def app_timezone() -> ZoneInfo:
    try:
        return ZoneInfo(settings.timezone)
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def reset_timezone_cache() -> None:
    app_timezone.cache_clear()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def local_now() -> datetime:
    return utc_now().astimezone(app_timezone())


def local_today() -> date:
    return local_now().date()


def to_local(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(app_timezone())


def day_bounds_utc(target_date: date | None = None) -> tuple[datetime, datetime]:
    active_date = target_date or local_today()
    tz = app_timezone()
    start_local = datetime.combine(active_date, time.min, tzinfo=tz)
    end_local = datetime.combine(active_date, time.max, tzinfo=tz)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)
