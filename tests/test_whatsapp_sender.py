"""Tests for WhatsApp sender with retry logic."""
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from delivery.whatsapp_sender import _send_whatsapp_message, send_digest
from db.models import Subscriber, Summary


@pytest.fixture
def mock_subscriber():
    """Create mock subscriber."""
    sub = MagicMock(spec=Subscriber)
    sub.id = 1
    sub.phone_number = "551234567890"
    sub.preferences = {"tech": True, "politics": False}
    sub.active = True
    return sub


@pytest.fixture
def mock_summary():
    """Create mock summary."""
    summary = MagicMock(spec=Summary)
    summary.id = 1
    summary.category = "tech"
    summary.period = "morning"
    summary.summary_text = "Tech news today"
    summary.key_takeaways = ["Point 1", "Point 2"]
    return summary


class MockAsyncClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def post(self, url, *, json, headers):
        self.calls.append((url, json, headers))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class MockResponse:
    def __init__(self, payload=None, status_code=200):
        self.payload = payload or {"success": True}
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "HTTP error",
                request=httpx.Request("POST", "http://bridge/send"),
                response=httpx.Response(self.status_code),
            )

    def json(self):
        return self.payload


@pytest.mark.asyncio
async def test_whatsapp_send_success(monkeypatch):
    """Test successful WhatsApp message send."""
    from config.settings import settings

    client = MockAsyncClient([MockResponse()])
    monkeypatch.setattr(settings, "whatsapp_bridge_url", "http://localhost:3000")
    monkeypatch.setattr(settings, "whatsapp_bridge_token", "")

    with patch("delivery.whatsapp_sender.httpx.AsyncClient", return_value=client):
        result = await _send_whatsapp_message("551234567890", "Hello")

    assert result == {"success": True}
    assert client.calls == [
        (
            "http://localhost:3000/send",
            {"number": "551234567890@s.whatsapp.net", "text": "Hello"},
            {},
        )
    ]


@pytest.mark.asyncio
async def test_whatsapp_send_includes_bearer_when_configured(monkeypatch):
    """Bridge /send receives Authorization when token is set."""
    from config.settings import settings

    client = MockAsyncClient([MockResponse()])
    monkeypatch.setattr(settings, "whatsapp_bridge_token", "secret-bridge-token")

    with patch("delivery.whatsapp_sender.httpx.AsyncClient", return_value=client):
        await _send_whatsapp_message("551234567890", "Hello")

    assert client.calls[0][2]["Authorization"] == "Bearer secret-bridge-token"


@pytest.mark.asyncio
async def test_whatsapp_send_timeout_retry():
    """Test that timeout triggers retry attempts with correct backoff."""
    client = MockAsyncClient([
        httpx.TimeoutException("Connection timeout"),
        httpx.TimeoutException("Connection timeout"),
        MockResponse(),
    ])

    with patch("delivery.whatsapp_sender.httpx.AsyncClient", return_value=client), \
         patch("delivery.whatsapp_sender.asyncio.sleep", AsyncMock()) as mock_sleep:
        result = await _send_whatsapp_message("551234567890", "Hello")

    assert result == {"success": True}
    assert len(client.calls) == 3
    assert mock_sleep.await_count == 2
    mock_sleep.assert_any_await(1)
    mock_sleep.assert_any_await(5)


@pytest.mark.asyncio
async def test_whatsapp_send_connection_error_retry():
    """Test that connection error triggers retry."""
    client = MockAsyncClient([
        httpx.ConnectError("Failed to connect"),
        MockResponse(),
    ])

    with patch("delivery.whatsapp_sender.httpx.AsyncClient", return_value=client), \
         patch("delivery.whatsapp_sender.asyncio.sleep", AsyncMock()) as mock_sleep:
        result = await _send_whatsapp_message("551234567890", "Hello")

    assert result == {"success": True}
    assert len(client.calls) == 2
    mock_sleep.assert_awaited_once_with(1)


@pytest.mark.asyncio
async def test_whatsapp_send_4xx_no_retry():
    """Test that 4xx errors don't trigger retry."""
    client = MockAsyncClient([MockResponse(status_code=400)])

    with patch("delivery.whatsapp_sender.httpx.AsyncClient", return_value=client):
        result = await _send_whatsapp_message("551234567890", "Hello")

    assert result is None
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_whatsapp_send_5xx_no_retry():
    """Test that HTTP status errors return None without retry."""
    client = MockAsyncClient([MockResponse(status_code=500)])

    with patch("delivery.whatsapp_sender.httpx.AsyncClient", return_value=client), \
         patch("delivery.whatsapp_sender.asyncio.sleep", AsyncMock()) as mock_sleep:
        result = await _send_whatsapp_message("551234567890", "Hello")

    assert result is None
    assert len(client.calls) == 1
    mock_sleep.assert_not_awaited()


@pytest.mark.asyncio
async def test_whatsapp_send_max_retries_reached():
    """Test that reaching max retries returns None."""
    client = MockAsyncClient([
        httpx.TimeoutException("Timeout"),
        httpx.TimeoutException("Timeout"),
        httpx.TimeoutException("Timeout"),
    ])

    with patch("delivery.whatsapp_sender.httpx.AsyncClient", return_value=client), \
         patch("delivery.whatsapp_sender.asyncio.sleep", AsyncMock()) as mock_sleep:
        result = await _send_whatsapp_message("551234567890", "Hello")

    assert result is None
    assert len(client.calls) == 3
    assert mock_sleep.await_count == 2


@pytest.mark.asyncio
async def test_send_digest_only_updates_last_sent_on_success(mock_subscriber, mock_summary):
    """Test that last_sent_at is only updated if at least one message succeeded."""
    subscribers = [mock_subscriber]
    summaries = [mock_summary]
    
    with patch('delivery.whatsapp_sender._send_whatsapp_message') as mock_send, \
         patch('delivery.whatsapp_sender.async_session') as mock_session_cls, \
         patch('delivery.whatsapp_sender.filter_summaries_by_preferences') as mock_filter, \
         patch('delivery.whatsapp_sender.format_digest') as mock_format, \
         patch('delivery.whatsapp_sender.split_message') as mock_split, \
         patch('delivery.whatsapp_sender.rate_limiter') as mock_limiter, \
         patch('delivery.whatsapp_sender._log_delivery_results', AsyncMock()):
        
        # Setup mocks
        mock_filter.return_value = summaries
        mock_format.return_value = "Formatted digest"
        mock_split.return_value = ["Single part"]  # Only 1 part to avoid mock list issues
        mock_limiter.acquire = AsyncMock()
        
        mock_session = MagicMock()
        mock_session_cls.return_value.__aenter__.return_value = mock_session
        mock_session.commit = AsyncMock()
        mock_session.close = AsyncMock()
        mock_session.get = AsyncMock(return_value=mock_subscriber)
        
        # Simulate successful send
        mock_send.return_value = {"success": True}
        
        sent = await send_digest(subscribers, summaries, "morning")
        
        assert sent == 1
        assert mock_session.commit.called


@pytest.mark.asyncio
async def test_send_digest_skips_update_on_all_failures(mock_subscriber, mock_summary):
    """Test that last_sent_at is NOT updated if all messages failed."""
    subscribers = [mock_subscriber]
    summaries = [mock_summary]
    
    with patch('delivery.whatsapp_sender._send_whatsapp_message') as mock_send, \
         patch('delivery.whatsapp_sender.async_session') as mock_session_cls, \
         patch('delivery.whatsapp_sender.filter_summaries_by_preferences') as mock_filter, \
         patch('delivery.whatsapp_sender.format_digest') as mock_format, \
         patch('delivery.whatsapp_sender.split_message') as mock_split, \
         patch('delivery.whatsapp_sender.rate_limiter') as mock_limiter, \
         patch('delivery.whatsapp_sender._log_delivery_results', AsyncMock()):
        
        # Setup mocks
        mock_filter.return_value = summaries
        mock_format.return_value = "Formatted digest"
        mock_split.return_value = ["Part 1"]
        mock_limiter.acquire = AsyncMock()
        
        # All calls fail
        mock_send.return_value = None
        
        mock_session = MagicMock()
        mock_session_cls.return_value.__aenter__.return_value = mock_session
        mock_session.commit = AsyncMock()
        mock_session.close = AsyncMock()
        mock_session.get = AsyncMock(return_value=mock_subscriber)
        
        # Mock time.sleep inside send_digest (asyncio.sleep)
        with patch('delivery.whatsapp_sender.asyncio.sleep', AsyncMock()):
            sent = await send_digest(subscribers, summaries, "morning")
        
        # Should have sent 0 messages
        assert sent == 0
        assert not mock_session.commit.called
