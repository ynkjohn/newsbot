"""Tests for WhatsApp sender with retry logic."""
import requests
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from requests.exceptions import ConnectionError, Timeout

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


def test_whatsapp_send_success():
    """Test successful WhatsApp message send."""
    with patch('delivery.whatsapp_sender.requests.post') as mock_post:
        mock_response = MagicMock()
        mock_response.json.return_value = {"success": True}
        mock_post.return_value = mock_response

        result = _send_whatsapp_message("551234567890", "Hello")

        assert result == {"success": True}
        mock_post.assert_called_once()


def test_whatsapp_send_includes_bearer_when_configured(monkeypatch):
    """Bridge /send receives Authorization when token is set."""
    from config.settings import settings

    monkeypatch.setattr(settings, "whatsapp_bridge_token", "secret-bridge-token")
    with patch("delivery.whatsapp_sender.requests.post") as mock_post:
        mock_response = MagicMock()
        mock_response.json.return_value = {"success": True}
        mock_post.return_value = mock_response

        _send_whatsapp_message("551234567890", "Hello")

        _, kwargs = mock_post.call_args
        assert kwargs["headers"]["Authorization"] == "Bearer secret-bridge-token"


@patch('delivery.whatsapp_sender.time.sleep')
def test_whatsapp_send_timeout_retry(mock_sleep):
    """Test that timeout triggers retry attempts with correct backoff."""
    with patch('delivery.whatsapp_sender.requests.post') as mock_post:
        # First 2 calls timeout, 3rd succeeds
        mock_response = MagicMock()
        mock_response.json.return_value = {"success": True}
        
        mock_post.side_effect = [
            Timeout("Connection timeout"),
            Timeout("Connection timeout"),
            mock_response
        ]
        
        result = _send_whatsapp_message("551234567890", "Hello")
        
        # Should succeed on 3rd attempt
        assert result == {"success": True}
        assert mock_post.call_count == 3
        # Should have slept for 1s and 5s
        assert mock_sleep.call_count == 2
        mock_sleep.assert_any_call(1)
        mock_sleep.assert_any_call(5)


@patch('delivery.whatsapp_sender.time.sleep')
def test_whatsapp_send_connection_error_retry(mock_sleep):
    """Test that connection error triggers retry."""
    with patch('delivery.whatsapp_sender.requests.post') as mock_post:
        mock_response = MagicMock()
        mock_response.json.return_value = {"success": True}
        
        mock_post.side_effect = [
            ConnectionError("Failed to connect"),
            mock_response
        ]
        
        result = _send_whatsapp_message("551234567890", "Hello")
        
        assert result == {"success": True}
        assert mock_post.call_count == 2
        mock_sleep.assert_called_once_with(1)


def test_whatsapp_send_4xx_no_retry():
    """Test that 4xx errors don't trigger retry."""
    with patch('delivery.whatsapp_sender.requests.post') as mock_post:
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_post.return_value = mock_response
        mock_response.raise_for_status.side_effect = requests.exceptions.HTTPError(response=mock_response)
        
        result = _send_whatsapp_message("551234567890", "Hello")
        
        # Should return None immediately, no retries
        assert result is None
        # Should be called only once
        assert mock_post.call_count == 1


@patch('delivery.whatsapp_sender.time.sleep')
def test_whatsapp_send_5xx_retry(mock_sleep):
    """Test that 5xx errors trigger retry and backoff."""
    with patch('delivery.whatsapp_sender.requests.post') as mock_post:
        # First call is 500, second succeeds
        error_response = MagicMock()
        error_response.status_code = 500
        error_response.raise_for_status.side_effect = requests.exceptions.HTTPError(response=error_response)
        
        success_response = MagicMock()
        success_response.json.return_value = {"success": True}
        
        mock_post.side_effect = [
            error_response,
            success_response
        ]
        
        result = _send_whatsapp_message("551234567890", "Hello")
        
        # Should retry and eventually succeed
        assert result == {"success": True}
        assert mock_post.call_count == 2
        mock_sleep.assert_called_once_with(1)


@patch('delivery.whatsapp_sender.time.sleep')
def test_whatsapp_send_max_retries_reached(mock_sleep):
    """Test that reaching max retries returns None."""
    with patch('delivery.whatsapp_sender.requests.post') as mock_post:
        mock_post.side_effect = Timeout("Timeout")
        
        result = _send_whatsapp_message("551234567890", "Hello")
        
        assert result is None
        assert mock_post.call_count == 3
        assert mock_sleep.call_count == 2


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
