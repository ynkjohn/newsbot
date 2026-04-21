import asyncio
import json
import re
import threading
from typing import Any

import structlog
from openai import OpenAI, APITimeoutError, RateLimitError, APIConnectionError, APIStatusError

from config.settings import settings
from processor.prompts import CORRECTION_PROMPT

logger = structlog.get_logger()


class LLMClient:
    """Wrapper around OpenRouter (primary) + OpenAI (fallback).

    OpenRouter is OpenAI-compatible, just uses a different base_url.
    Features: exponential backoff retry, timeouts, error differentiation.
    """

    def __init__(self):
        self._primary: OpenAI | None = None
        self._fallback: OpenAI | None = None
        self._primary_model = settings.llm_model_primary
        self._fallback_model = settings.llm_model_fallback
        self._init_clients()

    def _init_clients(self):
        if settings.openrouter_api_key:
            self._primary = OpenAI(
                api_key=settings.openrouter_api_key,
                base_url=settings.openrouter_base_url,
                timeout=120.0,  # 120s timeout for primary (deepseek is slow)
            )

        if settings.openai_api_key:
            self._fallback = OpenAI(
                api_key=settings.openai_api_key,
                timeout=60.0,  # 60s timeout for fallback
            )

        if not self._primary and not self._fallback:
            raise RuntimeError(
                "No LLM API key configured. Set OPENROUTER_API_KEY or OPENAI_API_KEY."
            )

    async def _call_with_retry(
        self,
        client: OpenAI,
        model: str,
        messages: list[dict],
        max_tokens: int,
        max_retries: int = 3,
    ) -> str:
        """Call LLM with exponential backoff retry logic.
        
        Differentiates between error types:
        - Timeout: retry with exponential backoff
        - Rate limit: retry with longer backoff
        - Server error (5xx): retry with backoff
        - Client error (4xx): don't retry
        """
        backoff_base = 1.0  # Start with 1 second
        
        for attempt in range(1, max_retries + 1):
            try:
                logger.debug(f"LLM call attempt {attempt}/{max_retries} with {model}")
                
                # Use asyncio.wait_for to ensure timeout (matching client timeout)
                response = await asyncio.wait_for(
                    asyncio.to_thread(
                        client.chat.completions.create,
                        model=model,
                        messages=messages,
                        max_tokens=max_tokens,
                        temperature=0.3,
                    ),
                    timeout=150.0,  # Allow up to 150s for detailed summaries
                )
                return response.choices[0].message.content
                
            except (APITimeoutError, asyncio.TimeoutError) as e:
                # Timeout - retry with backoff
                if attempt < max_retries:
                    wait_time = backoff_base * (2 ** (attempt - 1))
                    logger.warning(
                        f"LLM timeout on attempt {attempt}/{max_retries}, "
                        f"waiting {wait_time}s before retry: {type(e).__name__}"
                    )
                    await asyncio.sleep(wait_time)
                    continue
                else:
                    logger.error(f"LLM timeout after {max_retries} attempts")
                    raise
                    
            except RateLimitError:
                # Rate limit - retry with longer backoff
                if attempt < max_retries:
                    wait_time = backoff_base * (2 ** attempt)  # Exponential increase
                    logger.warning(
                        f"LLM rate limited on attempt {attempt}/{max_retries}, "
                        f"waiting {wait_time}s before retry"
                    )
                    await asyncio.sleep(wait_time)
                    continue
                else:
                    logger.error(f"LLM rate limited after {max_retries} attempts")
                    raise
                    
            except (APIConnectionError, APIStatusError) as e:
                # Retry: no HTTP status (transient network), 5xx, or APIConnectionError
                status_code = getattr(e, "status_code", None)
                transient = isinstance(e, APIConnectionError) or status_code is None
                server_error = status_code is not None and 500 <= status_code <= 599
                if (transient or server_error) and attempt < max_retries:
                    wait_time = backoff_base * (2 ** (attempt - 1))
                    logger.warning(
                        f"LLM retryable error on attempt {attempt}/{max_retries} "
                        f"(status={status_code}, type={type(e).__name__}), waiting {wait_time}s"
                    )
                    await asyncio.sleep(wait_time)
                    continue
                logger.error(f"LLM error (giving up): {type(e).__name__}: {e}")
                raise

        raise RuntimeError(f"LLM failed after {max_retries} attempts")

    def chat(self, system_prompt: str, user_prompt: str, max_tokens: int = 2000) -> str:
        """Send a chat completion request synchronously.
        
        Tries primary (OpenRouter) with retry, then fallback (OpenAI).
        WARNING: Potential event loop conflicts. Prefer _chat_async() when possible.
        """
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                logger.warning("chat() called from async context - use _chat_async() directly")
                raise RuntimeError("Sync chat() cannot be called from async context")
            return loop.run_until_complete(self._chat_async(system_prompt, user_prompt, max_tokens))
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                return loop.run_until_complete(self._chat_async(system_prompt, user_prompt, max_tokens))
            finally:
                loop.close()

    async def _chat_async(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 2000,
        model_override: str | None = None,
    ) -> str:
        """Async implementation of chat with retry logic."""
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        target_model = model_override or self._primary_model

        # Try primary (OpenRouter) with retries
        if self._primary:
            try:
                return await self._call_with_retry(
                    self._primary, target_model, messages, max_tokens
                )
            except Exception as e:
                logger.warning(
                    f"Primary LLM ({target_model}) failed after retries: "
                    f"{type(e).__name__}: {e}"
                )

        # Fallback (OpenAI direct) with retries
        if self._fallback:
            try:
                return await self._call_with_retry(
                    self._fallback, self._fallback_model, messages, max_tokens
                )
            except Exception as e:
                logger.error(
                    f"Fallback LLM ({self._fallback_model}) also failed: "
                    f"{type(e).__name__}: {e}"
                )
                raise

        raise RuntimeError("No LLM client available")

    def _extract_json_from_markdown(self, text: str) -> str:
        """Extract JSON from markdown code fences, handling edge cases.
        
        Handles: ```json\n{...}\n``` or just {...}
        """
        text = text.strip()
        
        # Try regex match for ```json...``` blocks (multiline safe)
        match = re.search(r'```(?:json)?\s*\n?([\s\S]*?)\n?```', text)
        if match:
            return match.group(1).strip()
        
        # If no markdown, return as-is
        return text

    def chat_json(self, system_prompt: str, user_prompt: str, max_tokens: int = 2000) -> dict[str, Any]:
        """Send a chat request and parse the response as JSON.
        
        Retries once with correction prompt if JSON parsing fails.
        WARNING: Same event loop conflict as chat(). Prefer chat_json_async() when possible.
        """
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                logger.warning("chat_json() called from async context - use chat_json_async() directly")
                raise RuntimeError("Sync chat_json() cannot be called from async context")
            return loop.run_until_complete(self.chat_json_async(system_prompt, user_prompt, max_tokens))
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                return loop.run_until_complete(self.chat_json_async(system_prompt, user_prompt, max_tokens))
            finally:
                loop.close()

    async def chat_json_async(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 2000,
        model_override: str | None = None,
    ) -> dict[str, Any]:
        """Async implementation of chat_json with retry and error handling."""
        raw = await self._chat_async(
            system_prompt,
            user_prompt,
            max_tokens,
            model_override=model_override,
        )

        # Strip markdown code fences if present
        cleaned = self._extract_json_from_markdown(raw)

        try:
            return json.loads(cleaned)
        except json.JSONDecodeError as e:
            logger.warning(
                f"LLM returned invalid JSON on first attempt, "
                f"retrying with correction prompt. Error: {e.msg} at line {e.lineno}"
            )

        # Retry with correction - only ONE retry
        try:
            corrected = await self._chat_async(
                system_prompt,
                f"{user_prompt}\n\n{CORRECTION_PROMPT}",
                max_tokens,
                model_override=model_override,
            )

            cleaned = self._extract_json_from_markdown(corrected)
            return json.loads(cleaned)
        except json.JSONDecodeError as e:
            logger.error(
                f"LLM still returned invalid JSON after correction attempt. "
                f"Error: {e.msg} at line {e.lineno}. "
                f"Response preview: {corrected[:200]}"
            )
            raise ValueError(
                f"LLM returned unparseable JSON after retry. Error: {e.msg}"
            )

    @property
    def model_name(self) -> str:
        """Return the name of the model being used."""
        if self._primary:
            return self._primary_model
        return f"openai/{self._fallback_model}"


# Singleton (sync entrypoint; guarded for concurrent first access from async tasks)
_client: LLMClient | None = None
_client_lock = threading.Lock()


def get_llm_client() -> LLMClient:
    """Return the singleton LLMClient, creating it if needed (thread-safe)."""
    global _client
    with _client_lock:
        if _client is None:
            _client = LLMClient()
        return _client
