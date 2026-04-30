"""Single source of truth for valid pipeline periods.

Every module that needs to validate, iterate, or display periods should
import from here instead of hardcoding its own set/dict.
"""

from __future__ import annotations

from typing import Final

# Canonical ordered tuple — order matches scheduler registration.
VALID_PERIODS: Final[tuple[str, ...]] = ("morning", "midday", "afternoon", "evening")

VALID_PERIODS_SET: Final[frozenset[str]] = frozenset(VALID_PERIODS)


def is_valid_period(period: str) -> bool:
    """Return True when *period* is one of the four recognised pipeline slots."""
    return period in VALID_PERIODS_SET


def validate_period(period: str) -> str:
    """Return *period* unchanged or raise ``ValueError``."""
    if period not in VALID_PERIODS_SET:
        raise ValueError(
            f"Invalid period '{period}'. Must be one of: {', '.join(VALID_PERIODS)}"
        )
    return period


def period_display_name(period: str) -> str:
    """Human-readable Portuguese label used in digest headers and dashboard."""
    _LABELS: dict[str, str] = {
        "morning": "Manhã",
        "midday": "Meio-dia",
        "afternoon": "Tarde",
        "evening": "Noite",
    }
    return _LABELS.get(period, period.capitalize())
