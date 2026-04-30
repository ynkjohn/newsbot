"""Reusable async retry/backoff policy.

Provides a simple decorator-style helper for retrying async operations
with configurable backoff.  Used by both the WhatsApp bridge client and
the LLM client to standardise retry behaviour.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import structlog

logger = structlog.get_logger()


@dataclass(frozen=True)
class RetryPolicy:
    """Immutable retry configuration.

    Parameters
    ----------
    max_attempts:
        Total number of attempts (first call + retries).
    backoff_delays:
        Per-attempt delay in seconds *before* the next attempt.
        If there are fewer entries than ``max_attempts - 1``, the last
        value is reused.
    retryable_exceptions:
        Exception types that trigger a retry.  All others propagate
        immediately.
    """

    max_attempts: int = 3
    backoff_delays: Sequence[float] = (1.0, 5.0, 10.0)
    retryable_exceptions: tuple[type[BaseException], ...] = (Exception,)

    # --- Derived helpers ---

    def delay_for_attempt(self, attempt: int) -> float:
        """Return the sleep duration before attempt *attempt* (1-indexed)."""
        idx = min(attempt - 1, len(self.backoff_delays) - 1)
        return self.backoff_delays[idx]


# ---------------------------------------------------------------------------
# Pre-built policies (preserve the current effective timings)
# ---------------------------------------------------------------------------

#: WhatsApp bridge: 3 attempts, delays [1, 5, 10]s
WHATSAPP_RETRY = RetryPolicy(
    max_attempts=3,
    backoff_delays=(1.0, 5.0, 10.0),
)

#: LLM primary/fallback: 3 attempts, exponential backoff 1s→2s→4s
LLM_RETRY = RetryPolicy(
    max_attempts=3,
    backoff_delays=(1.0, 2.0, 4.0),
)
