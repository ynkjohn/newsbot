import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import structlog

from config.settings import settings

logger = structlog.get_logger()

CONFIG_PATH = settings.base_dir / "data" / "llm_config.json"
MASK_PREFIX = "********"

PROVIDER_OPTIONS: dict[str, dict[str, Any]] = {
    "openrouter": {
        "label": "OpenRouter",
        "default_base_url": "https://openrouter.ai/api/v1",
        "timeout": 120.0,
        "models": [
            "deepseek/deepseek-v3.2",
            "deepseek/deepseek-chat",
            "qwen/qwen3-235b-a22b-2507",
            "openai/gpt-4o-mini",
        ],
    },
    "deepseek": {
        "label": "DeepSeek direto",
        "default_base_url": "https://api.deepseek.com",
        "timeout": 120.0,
        "models": ["deepseek-chat", "deepseek-reasoner"],
    },
    "openai": {
        "label": "OpenAI",
        "default_base_url": "",
        "timeout": 60.0,
        "models": ["gpt-4o-mini"],
    },
}


class LLMConfigError(ValueError):
    pass


@dataclass(frozen=True)
class LLMRuntimeConfig:
    provider: str
    model: str
    base_url: str
    api_keys: dict[str, str] = field(default_factory=dict)

    @property
    def api_key(self) -> str:
        return self.api_keys.get(self.provider, "")

    @property
    def timeout(self) -> float:
        return float(PROVIDER_OPTIONS[self.provider]["timeout"])


def mask_api_key(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 4:
        return MASK_PREFIX
    return f"{MASK_PREFIX}{value[-4:]}"


def _is_masked_key(value: str) -> bool:
    return (bool(value) and set(value) == {"*"}) or (bool(value) and value.startswith(MASK_PREFIX))


def _settings_api_keys() -> dict[str, str]:
    return {
        "openrouter": settings.openrouter_api_key,
        "deepseek": "",
        "openai": settings.openai_api_key,
    }


def _default_config() -> LLMRuntimeConfig:
    return LLMRuntimeConfig(
        provider="openrouter",
        model=settings.llm_model_primary,
        base_url=settings.openrouter_base_url or PROVIDER_OPTIONS["openrouter"]["default_base_url"],
        api_keys=_settings_api_keys(),
    )


def _valid_url(value: str, provider: str) -> bool:
    if provider == "openai" and not value:
        return True
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


class LLMConfigStore:
    def __init__(self, path: Path = CONFIG_PATH):
        self.path = Path(path)

    def load(self) -> LLMRuntimeConfig:
        defaults = _default_config()
        if not self.path.exists():
            return defaults

        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("llm_config_load_failed", error=str(exc))
            return defaults

        if not isinstance(raw, dict):
            logger.warning("llm_config_invalid_shape")
            return defaults

        provider = str(raw.get("provider") or defaults.provider)
        if provider not in PROVIDER_OPTIONS:
            logger.warning("llm_config_unknown_provider", provider=provider)
            return defaults

        persisted_api_keys = raw.get("api_keys", {})
        if not isinstance(persisted_api_keys, dict):
            logger.warning("llm_config_invalid_api_keys")
            return defaults

        api_keys = {**defaults.api_keys, **{k: str(v or "") for k, v in persisted_api_keys.items()}}
        model = str(raw.get("model") or defaults.model).strip()
        base_url = str(raw.get("base_url") or PROVIDER_OPTIONS[provider]["default_base_url"]).strip()

        try:
            self._validate(provider, model, base_url, api_keys.get(provider, ""))
        except LLMConfigError as exc:
            logger.warning("llm_config_invalid_persisted", error=str(exc))
            return defaults

        return LLMRuntimeConfig(provider=provider, model=model, base_url=base_url, api_keys=api_keys)

    def save(self, payload: dict[str, Any]) -> LLMRuntimeConfig:
        current = self.load()
        provider = str(payload.get("provider") or current.provider).strip()
        model = str(payload.get("model") or "").strip()
        base_url = str(payload.get("base_url") or "").strip()
        api_key = str(payload.get("api_key") or "")

        if provider not in PROVIDER_OPTIONS:
            raise LLMConfigError("Provider desconhecido.")

        if not base_url:
            base_url = str(PROVIDER_OPTIONS[provider]["default_base_url"])

        api_keys = dict(current.api_keys)
        if api_key and not _is_masked_key(api_key):
            api_keys[provider] = api_key

        self._validate(provider, model, base_url, api_keys.get(provider, ""))

        data = {
            "provider": provider,
            "model": model,
            "base_url": base_url,
            "api_keys": api_keys,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return LLMRuntimeConfig(provider=provider, model=model, base_url=base_url, api_keys=api_keys)

    def build_unsaved(self, payload: dict[str, Any]) -> LLMRuntimeConfig:
        current = self.load()
        provider = str(payload.get("provider") or current.provider).strip()
        model = str(payload.get("model") or "").strip()
        base_url = str(payload.get("base_url") or "").strip()
        api_key = str(payload.get("api_key") or "")

        if provider not in PROVIDER_OPTIONS:
            raise LLMConfigError("Provider desconhecido.")

        if not base_url:
            base_url = str(PROVIDER_OPTIONS[provider]["default_base_url"])

        api_keys = dict(current.api_keys)
        if api_key and not _is_masked_key(api_key):
            api_keys[provider] = api_key

        self._validate(provider, model, base_url, api_keys.get(provider, ""))
        return LLMRuntimeConfig(provider=provider, model=model, base_url=base_url, api_keys=api_keys)

    def public_payload(self) -> dict[str, Any]:
        config = self.load()
        return public_payload(config)

    def _validate(self, provider: str, model: str, base_url: str, api_key: str) -> None:
        if provider not in PROVIDER_OPTIONS:
            raise LLMConfigError("Provider desconhecido.")
        if not model:
            raise LLMConfigError("Modelo é obrigatório.")
        if not _valid_url(base_url, provider):
            raise LLMConfigError("Base URL deve usar http ou https.")
        if not api_key:
            raise LLMConfigError("API key é obrigatória para o provider ativo.")


def public_payload(config: LLMRuntimeConfig) -> dict[str, Any]:
    providers = {}
    for provider, meta in PROVIDER_OPTIONS.items():
        key = config.api_keys.get(provider, "")
        providers[provider] = {
            "label": meta["label"],
            "defaultBaseUrl": meta["default_base_url"],
            "models": list(meta["models"]),
            "configured": bool(key),
            "apiKeyMasked": mask_api_key(key),
        }

    return {
        "provider": config.provider,
        "model": config.model,
        "baseUrl": config.base_url,
        "apiKeyMasked": mask_api_key(config.api_key),
        "providers": providers,
    }


_store = LLMConfigStore()


def get_llm_config_store() -> LLMConfigStore:
    return _store


def get_active_llm_config() -> LLMRuntimeConfig:
    return _store.load()
