import asyncio
import time

import structlog

logger = structlog.get_logger()


class TokenBucketRateLimiter:
    """Token bucket rate limiter for Twilio API calls.

    Default: 1 message/second (Twilio sandbox limit).
    Production WhatsApp Business API can be configured higher.
    """

    def __init__(self, rate: float = 1.0, burst: int = 5):
        self.rate = rate  # tokens per second
        self.burst = burst  # max tokens accumulated
        self.tokens = float(burst)
        self.last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self.last_refill
            self.tokens = min(self.burst, self.tokens + elapsed * self.rate)
            self.last_refill = now

            if self.tokens >= 1.0:
                self.tokens -= 1.0
                return

            wait_time = (1.0 - self.tokens) / self.rate
            await asyncio.sleep(wait_time)
            self.tokens = 0.0
