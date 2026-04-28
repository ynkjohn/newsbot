"""Test configuration and fixtures."""
import asyncio
import platform
from unittest.mock import AsyncMock

import pytest

platform.machine = lambda: "AMD64"

from config.settings import settings  # noqa: E402
from config.time_utils import reset_timezone_cache  # noqa: E402


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch):
    monkeypatch.setattr(settings, "allowed_numbers", "")
    monkeypatch.setattr(settings, "whatsapp_bridge_token", "test-bridge-token")
    monkeypatch.setattr(settings, "whatsapp_bridge_url", "http://whatsapp-bridge:3000")
    monkeypatch.setattr(settings, "admin_auth_enabled", True)
    monkeypatch.setattr(settings, "admin_username", "admin")
    monkeypatch.setattr(settings, "admin_password", "test-admin-password")
    monkeypatch.setattr(settings, "timezone", "America/Sao_Paulo")
    monkeypatch.setattr(settings, "pipeline_hours", "7,12,17,21")
    reset_timezone_cache()
    yield
    reset_timezone_cache()


@pytest.fixture
def async_mock():
    return AsyncMock


pytest_plugins = ("pytest_asyncio",)
