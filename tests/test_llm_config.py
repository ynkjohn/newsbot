import json

import pytest

from processor.llm_config import (
    LLMConfigError,
    LLMConfigStore,
    mask_api_key,
)


def test_load_defaults_when_file_missing(tmp_path, monkeypatch):
    store = LLMConfigStore(path=tmp_path / "llm_config.json")
    monkeypatch.setattr("processor.llm_config.settings.openrouter_api_key", "or-key")
    monkeypatch.setattr("processor.llm_config.settings.openai_api_key", "oa-key")
    monkeypatch.setattr("processor.llm_config.settings.openrouter_base_url", "https://openrouter.ai/api/v1")
    monkeypatch.setattr("processor.llm_config.settings.llm_model_primary", "qwen/qwen3-235b-a22b-2507")
    monkeypatch.setattr("processor.llm_config.settings.llm_model_fallback", "openai/gpt-4o-mini")

    config = store.load()

    assert config.provider == "openrouter"
    assert config.model == "qwen/qwen3-235b-a22b-2507"
    assert config.base_url == "https://openrouter.ai/api/v1"
    assert config.api_keys["openrouter"] == "or-key"
    assert config.api_keys["openai"] == "oa-key"


def test_save_and_reload_persisted_config(tmp_path):
    store = LLMConfigStore(path=tmp_path / "llm_config.json")

    saved = store.save(
        {
            "provider": "deepseek",
            "model": "deepseek-chat",
            "base_url": "https://api.deepseek.com",
            "api_key": "deepseek-key",
        }
    )
    loaded = store.load()

    assert saved.provider == "deepseek"
    assert loaded.provider == "deepseek"
    assert loaded.model == "deepseek-chat"
    assert loaded.base_url == "https://api.deepseek.com"
    assert loaded.api_keys["deepseek"] == "deepseek-key"


def test_masked_or_empty_api_key_preserves_existing_key(tmp_path):
    store = LLMConfigStore(path=tmp_path / "llm_config.json")
    store.save(
        {
            "provider": "deepseek",
            "model": "deepseek-chat",
            "base_url": "https://api.deepseek.com",
            "api_key": "deepseek-key",
        }
    )

    store.save(
        {
            "provider": "deepseek",
            "model": "deepseek-reasoner",
            "base_url": "https://api.deepseek.com/v1",
            "api_key": "************-key",
        }
    )
    assert store.load().api_keys["deepseek"] == "deepseek-key"

    store.save(
        {
            "provider": "deepseek",
            "model": "deepseek-chat",
            "base_url": "https://api.deepseek.com",
            "api_key": "",
        }
    )
    assert store.load().api_keys["deepseek"] == "deepseek-key"


def test_public_payload_never_exposes_api_key(tmp_path):
    store = LLMConfigStore(path=tmp_path / "llm_config.json")
    store.save(
        {
            "provider": "deepseek",
            "model": "deepseek-chat",
            "base_url": "https://api.deepseek.com",
            "api_key": "sk-super-secret",
        }
    )

    payload = store.public_payload()

    assert "sk-super-secret" not in json.dumps(payload)
    assert payload["apiKeyMasked"].startswith("********")
    assert payload["providers"]["deepseek"]["configured"] is True


def test_invalid_provider_model_url_and_missing_key_are_rejected(tmp_path):
    store = LLMConfigStore(path=tmp_path / "llm_config.json")

    with pytest.raises(LLMConfigError, match="Provider desconhecido"):
        store.save({"provider": "other", "model": "x", "base_url": "https://example.com", "api_key": "key"})

    with pytest.raises(LLMConfigError, match="Modelo é obrigatório"):
        store.save({"provider": "deepseek", "model": "", "base_url": "https://api.deepseek.com", "api_key": "key"})

    with pytest.raises(LLMConfigError, match="Base URL"):
        store.save({"provider": "deepseek", "model": "deepseek-chat", "base_url": "ftp://example.com", "api_key": "key"})

    with pytest.raises(LLMConfigError, match="API key é obrigatória"):
        store.save({"provider": "deepseek", "model": "deepseek-chat", "base_url": "https://api.deepseek.com", "api_key": ""})


@pytest.mark.parametrize(
    "file_content",
    [
        "{broken json",
        "[]",
        '"bad"',
        '{"api_keys": []}',
        '{"api_keys": null}',
    ],
)
def test_corrupted_file_falls_back_to_defaults(tmp_path, monkeypatch, file_content):
    path = tmp_path / "llm_config.json"
    path.write_text(file_content, encoding="utf-8")
    store = LLMConfigStore(path=path)
    monkeypatch.setattr("processor.llm_config.settings.openrouter_api_key", "or-key")
    monkeypatch.setattr("processor.llm_config.settings.openrouter_base_url", "https://openrouter.ai/api/v1")
    monkeypatch.setattr("processor.llm_config.settings.llm_model_primary", "default-model")

    config = store.load()

    assert config.provider == "openrouter"
    assert config.model == "default-model"


def test_mask_api_key_handles_short_and_long_values():
    assert mask_api_key("") == ""
    assert mask_api_key("abcd") == "********"
    assert mask_api_key("sk-1234567890") == "********7890"
