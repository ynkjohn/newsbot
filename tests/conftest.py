"""Test configuration and fixtures."""
import asyncio
import pytest
from unittest.mock import AsyncMock


@pytest.fixture(scope="session")
def event_loop():
    """Create event loop for async tests."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def async_mock():
    """Create AsyncMock helper."""
    return AsyncMock


# Configure pytest-asyncio
pytest_plugins = ('pytest_asyncio',)
