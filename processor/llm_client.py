import asyncio
import json
import re
import threading
from dataclasses import asdict, dataclass
from typing import Any

import structlog
from openai import OpenAI, APITimeoutError, RateLimitError, APIConnectionError, APIStatusError

from config.settings import settings
from processor.llm_config import LLMRuntimeConfig, get_active_llm_config
from processor.prompts import CORRECTION_PROMPT

logger = structlog.get_logger()


DEEPSEEK_PRICING_USD_PER_1M = {
    "deepseek-v4-flash": {
        "input_cache_hit": 0.0028,
        "input_cache_miss": 0.14,
        "output": 0.28,
    },
    "deepseek-chat": {
        "input_cache_hit": 0.0028,
        "input_cache_miss": 0.14,
        "output": 0.28,
    },
    "deepseek-reasoner": {
        "input_cache_hit": 0.0028,
        "input_cache_miss": 0.14,
        "output": 0.28,
    },
    "deepseek-v4-pro": {
        "input_cache_hit": 0.003625,
        "input_cache_miss": 0.435,
        "output": 0.87,
    },
}


@dataclass(frozen=True)
class LLMUsage:
    provider: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    prompt_cache_hit_tokens: int = 0
    prompt_cache_miss_tokens: int = 0
    estimated_cost_usd: float | None = None

    def to_metadata(self) -> dict[str, Any]:
        data = asdict(self)
        if data["estimated_cost_usd"] is not None:
            data["estimated_cost_usd"] = round(float(data["estimated_cost_usd"]), 8)
        return data


@dataclass(frozen=True)
class LLMTextResponse:
    content: str
    usage: LLMUsage | None = None


def _direct_openai_model_name(model: str) -> str:
    return model.removeprefix("openai/")


def _is_mock_value(value: Any) -> bool:
    return value.__class__.__module__.startswith("unittest.mock")


def _usage_to_dict(usage: Any) -> dict[str, Any]:
    if usage is None or _is_mock_value(usage):
        return {}
    if isinstance(usage, dict):
        return usage
    if hasattr(usage, "model_dump"):
        return usage.model_dump()
    if hasattr(usage, "dict"):
        return usage.dict()
    return {
        key: getattr(usage, key, None)
        for key in (
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
            "prompt_cache_hit_tokens",
            "prompt_cache_miss_tokens",
            "prompt_tokens_details",
        )
    }


def _int_usage_value(data: dict[str, Any], key: str) -> int:
    value = data.get(key)
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _nested_usage_value(data: dict[str, Any], section: str, key: str) -> int:
    nested = data.get(section)
    if not isinstance(nested, dict):
        nested = _usage_to_dict(nested)
    return _int_usage_value(nested, key)


def _estimate_usage_cost_usd(
    provider: str,
    model: str,
    *,
    prompt_tokens: int,
    completion_tokens: int,
    prompt_cache_hit_tokens: int,
    prompt_cache_miss_tokens: int,
) -> float | None:
    if provider != "deepseek":
        return None

    pricing = DEEPSEEK_PRICING_USD_PER_1M.get(model.removeprefix("deepseek/"))
    if not pricing:
        return None

    cache_hit_tokens = max(0, prompt_cache_hit_tokens)
    if prompt_cache_miss_tokens:
        cache_miss_tokens = max(0, prompt_cache_miss_tokens)
    else:
        cache_miss_tokens = max(0, prompt_tokens - cache_hit_tokens)

    return (
        (cache_hit_tokens / 1_000_000) * pricing["input_cache_hit"]
        + (cache_miss_tokens / 1_000_000) * pricing["input_cache_miss"]
        + (completion_tokens / 1_000_000) * pricing["output"]
    )


def _usage_from_response(response: Any, provider: str, model: str) -> LLMUsage | None:
    usage_data = _usage_to_dict(getattr(response, "usage", None))
    if not usage_data:
        return None

    prompt_tokens = _int_usage_value(usage_data, "prompt_tokens")
    completion_tokens = _int_usage_value(usage_data, "completion_tokens")
    total_tokens = _int_usage_value(usage_data, "total_tokens") or prompt_tokens + completion_tokens
    prompt_cache_hit_tokens = (
        _int_usage_value(usage_data, "prompt_cache_hit_tokens")
        or _nested_usage_value(usage_data, "prompt_tokens_details", "cached_tokens")
    )
    prompt_cache_miss_tokens = _int_usage_value(usage_data, "prompt_cache_miss_tokens")
    estimated_cost_usd = _estimate_usage_cost_usd(
        provider,
        model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        prompt_cache_hit_tokens=prompt_cache_hit_tokens,
        prompt_cache_miss_tokens=prompt_cache_miss_tokens,
    )

    return LLMUsage(
        provider=provider,
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        prompt_cache_hit_tokens=prompt_cache_hit_tokens,
        prompt_cache_miss_tokens=prompt_cache_miss_tokens,
        estimated_cost_usd=estimated_cost_usd,
    )


def combine_llm_usage(*usages: LLMUsage | None) -> LLMUsage | None:
    valid_usages = [usage for usage in usages if usage is not None]
    if not valid_usages:
        return None
    if len(valid_usages) == 1:
        return valid_usages[0]

    providers = {usage.provider for usage in valid_usages}
    models = {usage.model for usage in valid_usages}
    estimated_costs = [
        usage.estimated_cost_usd
        for usage in valid_usages
        if usage.estimated_cost_usd is not None
    ]
    return LLMUsage(
        provider=valid_usages[0].provider if len(providers) == 1 else "mixed",
        model=valid_usages[0].model if len(models) == 1 else "mixed",
        prompt_tokens=sum(usage.prompt_tokens for usage in valid_usages),
        completion_tokens=sum(usage.completion_tokens for usage in valid_usages),
        total_tokens=sum(usage.total_tokens for usage in valid_usages),
        prompt_cache_hit_tokens=sum(usage.prompt_cache_hit_tokens for usage in valid_usages),
        prompt_cache_miss_tokens=sum(usage.prompt_cache_miss_tokens for usage in valid_usages),
        estimated_cost_usd=sum(estimated_costs) if estimated_costs else None,
    )


class LLMClient:
    """Wrapper around OpenRouter (primary) + OpenAI (fallback).

    OpenRouter is OpenAI-compatible, just uses a different base_url.
    Features: exponential backoff retry, timeouts, error differentiation.
    """

    def __init__(self):
        self._primary: OpenAI | None = None
        self._fallback: OpenAI | None = None
        self._active_config: LLMRuntimeConfig | None = None
        self._primary_model = settings.llm_model_primary
        self._fallback_model = _direct_openai_model_name(settings.llm_model_fallback)
        self._init_clients()

    def _openai_kwargs(self, api_key: str, base_url: str, timeout: float) -> dict[str, Any]:
        kwargs: dict[str, Any] = {"api_key": api_key, "timeout": timeout}
        if base_url:
            kwargs["base_url"] = base_url
        return kwargs

    def _init_clients(self):
        config = get_active_llm_config()
        self._active_config = config
        self._primary_model = config.model
        self._fallback_model = _direct_openai_model_name(settings.llm_model_fallback)

        if not config.api_key:
            raise RuntimeError(f"No API key configured for {config.provider}.")

        self._primary = OpenAI(
            **self._openai_kwargs(config.api_key, config.base_url, config.timeout)
        )

        # Keep OpenAI fallback for non-OpenAI active providers when configured.
        if config.provider != "openai" and config.api_keys.get("openai"):
            self._fallback = OpenAI(
                api_key=config.api_keys["openai"],
                timeout=60.0,  # 60s timeout for fallback
            )

    async def _call_with_retry(
        self,
        client: OpenAI,
        provider: str,
        model: str,
        messages: list[dict],
        max_tokens: int,
        max_retries: int = 3,
    ) -> LLMTextResponse:
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
                usage = _usage_from_response(response, provider, model)
                if usage:
                    logger.info("LLM usage", **usage.to_metadata())
                return LLMTextResponse(
                    content=response.choices[0].message.content or "",
                    usage=usage,
                )
                
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

        .. deprecated::
            Prefer ``chat_async()`` in async contexts to avoid event-loop
            conflicts.  This wrapper exists only for backward compatibility.
        """
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                logger.warning("chat() called from async context - use chat_async() directly")
                raise RuntimeError("Sync chat() cannot be called from async context")
            return loop.run_until_complete(self.chat_async(system_prompt, user_prompt, max_tokens))
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                return loop.run_until_complete(self.chat_async(system_prompt, user_prompt, max_tokens))
            finally:
                loop.close()

    async def chat_async_with_usage(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 8192,
        model_override: str | None = None,
    ) -> LLMTextResponse:
        """Async chat with primary → fallback retry logic.

        This is the **recommended** entry point for async callers.
        Retry policy: ``LLM_RETRY`` (3 attempts, exponential backoff).
        """
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        target_model = model_override or self._primary_model

        # Try primary (OpenRouter) with retries
        if self._primary:
            try:
                return await self._call_with_retry(
                    self._primary,
                    self._active_config.provider if self._active_config else "primary",
                    target_model,
                    messages,
                    max_tokens,
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
                    self._fallback,
                    "openai",
                    self._fallback_model,
                    messages,
                    max_tokens,
                )
            except Exception as e:
                logger.error(
                    f"Fallback LLM ({self._fallback_model}) also failed: "
                    f"{type(e).__name__}: {e}"
                )
                raise

        raise RuntimeError("No LLM client available")

    async def chat_async(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 8192,
        model_override: str | None = None,
    ) -> str:
        response = await self.chat_async_with_usage(
            system_prompt,
            user_prompt,
            max_tokens,
            model_override=model_override,
        )
        return response.content

    # Backward-compatible alias so existing ``client._chat_async(...)`` calls
    # keep working without modification during the migration window.
    _chat_async = chat_async

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

        .. deprecated::
            Prefer ``chat_json_async()`` in async contexts to avoid
            event-loop conflicts.
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
        max_tokens: int = 8192,
        model_override: str | None = None,
    ) -> dict[str, Any]:
        result, _usage = await self.chat_json_async_with_usage(
            system_prompt,
            user_prompt,
            max_tokens,
            model_override=model_override,
        )
        return result

    async def chat_json_async_with_usage(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 8192,
        model_override: str | None = None,
    ) -> tuple[dict[str, Any], LLMUsage | None]:
        """Async implementation of chat_json with retry and error handling."""
        raw_response = await self.chat_async_with_usage(
            system_prompt,
            user_prompt,
            max_tokens,
            model_override=model_override,
        )

        # Strip markdown code fences if present
        cleaned = self._extract_json_from_markdown(raw_response.content)

        try:
            return json.loads(cleaned), raw_response.usage
        except json.JSONDecodeError as e:
            logger.warning(
                f"LLM returned invalid JSON on first attempt, "
                f"retrying with correction prompt. Error: {e.msg} at line {e.lineno}"
            )

        # Retry with correction - only ONE retry
        try:
            corrected_response = await self.chat_async_with_usage(
                system_prompt,
                f"{user_prompt}\n\n{CORRECTION_PROMPT}",
                max_tokens,
                model_override=model_override,
            )

            cleaned = self._extract_json_from_markdown(corrected_response.content)
            return json.loads(cleaned), combine_llm_usage(raw_response.usage, corrected_response.usage)
        except json.JSONDecodeError as e:
            logger.error(
                f"LLM still returned invalid JSON after correction attempt. "
                f"Error: {e.msg} at line {e.lineno}. "
                f"Response preview: {corrected_response.content[:200]}"
            )
            raise ValueError(
                f"LLM returned unparseable JSON after retry. Error: {e.msg}"
            )

    @property
    def model_name(self) -> str:
        """Return the name of the model being used."""
        if self._active_config and self._primary:
            if self._active_config.provider == "openai":
                return f"openai/{self._primary_model}"
            return f"{self._active_config.provider}/{self._primary_model}"
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


async def test_llm_config(config: LLMRuntimeConfig) -> str:
    """Test an unsaved LLM config with a minimal chat request."""
    if not config.api_key:
        raise RuntimeError(f"No API key configured for {config.provider}.")

    kwargs: dict[str, Any] = {"api_key": config.api_key, "timeout": config.timeout}
    if config.base_url:
        kwargs["base_url"] = config.base_url
    client = OpenAI(**kwargs)
    response = await asyncio.to_thread(
        client.chat.completions.create,
        model=config.model,
        messages=[
            {"role": "system", "content": "Responda apenas com ok."},
            {"role": "user", "content": "Teste de conexão."},
        ],
        max_tokens=4,
    )
    content = response.choices[0].message.content if response.choices else ""
    return (content or "").strip()


def reset_llm_client() -> None:
    """Clear the singleton so the next access uses the active LLM config."""
    global _client
    with _client_lock:
        _client = None
