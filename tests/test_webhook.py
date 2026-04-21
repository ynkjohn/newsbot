"""Tests for webhook validation and processing."""
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app import app, WhatsAppWebhookPayload
from config.settings import settings


client = TestClient(app)

# Use a test token for webhook authentication
TEST_WEBHOOK_TOKEN = "test-webhook-token-12345"

@pytest.fixture(autouse=True)
def set_webhook_token(monkeypatch):
    """Set webhook token for tests."""
    monkeypatch.setattr(settings, "whatsapp_bridge_token", TEST_WEBHOOK_TOKEN)


def _get_webhook_headers():
    """Generate valid Authorization header for webhook tests."""
    return {"Authorization": f"Bearer {TEST_WEBHOOK_TOKEN}"}


def test_webhook_valid_payload():
    """Test webhook with valid payload."""
    payload = {
        "key": {"remoteJid": "551234567890@s.whatsapp.net"},
        "message": {"conversation": "Hello!"}
    }
    
    response = client.post("/webhook/whatsapp", json=payload, headers=_get_webhook_headers())
    assert response.status_code == 200


def test_webhook_invalid_json():
    """Test webhook with invalid JSON."""
    response = client.post(
        "/webhook/whatsapp",
        content="not json",
        headers={
            "Content-Type": "application/json",
            **_get_webhook_headers()
        }
    )
    assert response.status_code == 400
    assert "error" in response.json()


def test_webhook_missing_key():
    """Test webhook with missing 'key' field."""
    payload = {
        "message": {"conversation": "Hello!"}
    }
    
    response = client.post("/webhook/whatsapp", json=payload, headers=_get_webhook_headers())
    assert response.status_code == 422
    assert "error" in response.json()


def test_webhook_missing_message():
    """Test webhook with missing 'message' field."""
    payload = {
        "key": {"remoteJid": "551234567890@s.whatsapp.net"}
    }
    
    response = client.post("/webhook/whatsapp", json=payload, headers=_get_webhook_headers())
    assert response.status_code == 422


def test_webhook_empty_message():
    """Test webhook with empty conversation."""
    payload = {
        "key": {"remoteJid": "551234567890@s.whatsapp.net"},
        "message": {"conversation": "   "}  # Only whitespace
    }
    
    response = client.post("/webhook/whatsapp", json=payload, headers=_get_webhook_headers())
    # Will be 400 after stripping
    assert response.status_code in [400, 422]


def test_webhook_missing_remote_jid():
    """Test webhook with missing remoteJid."""
    payload = {
        "key": {},
        "message": {"conversation": "Hello!"}
    }
    
    response = client.post("/webhook/whatsapp", json=payload, headers=_get_webhook_headers())
    assert response.status_code == 422


def test_webhook_missing_authorization_header():
    """Test webhook rejects requests without Authorization header."""
    payload = {
        "key": {"remoteJid": "551234567890@s.whatsapp.net"},
        "message": {"conversation": "Hello!"}
    }
    
    response = client.post("/webhook/whatsapp", json=payload)
    assert response.status_code == 401
    assert "Unauthorized" in response.json()["error"]


def test_webhook_invalid_token():
    """Test webhook rejects requests with invalid token."""
    payload = {
        "key": {"remoteJid": "551234567890@s.whatsapp.net"},
        "message": {"conversation": "Hello!"}
    }
    
    response = client.post(
        "/webhook/whatsapp", 
        json=payload,
        headers={"Authorization": "Bearer invalid-token"}
    )
    assert response.status_code == 403
    assert "Forbidden" in response.json()["error"]


def test_pydantic_webhook_validation():
    """Test Pydantic model directly."""
    # Valid payload
    valid_payload = {
        "key": {"remoteJid": "551234567890@s.whatsapp.net"},
        "message": {"conversation": "Hello"}
    }
    model = WhatsAppWebhookPayload(**valid_payload)
    assert model.key.remoteJid == "551234567890@s.whatsapp.net"
    assert model.message.conversation == "Hello"


def test_pydantic_webhook_validation_empty_message():
    """Test Pydantic rejects empty message."""
    invalid_payload = {
        "key": {"remoteJid": "551234567890@s.whatsapp.net"},
        "message": {"conversation": ""}
    }
    
    with pytest.raises(ValidationError):
        WhatsAppWebhookPayload(**invalid_payload)


def test_pydantic_webhook_validation_empty_jid():
    """Test Pydantic rejects empty remoteJid."""
    invalid_payload = {
        "key": {"remoteJid": ""},
        "message": {"conversation": "Hello"}
    }
    
    with pytest.raises(ValidationError):
        WhatsAppWebhookPayload(**invalid_payload)


def test_health_endpoint():
    """Test health check endpoint."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_dashboard_accepts_list_key_takeaways(monkeypatch):
    """Dashboard should handle legacy list-shaped takeaways without crashing."""
    from datetime import datetime, timezone
    from unittest.mock import MagicMock
    import db.engine as db_engine

    summary = MagicMock()
    summary.id = 1
    summary.category = "tech"
    summary.period = "morning"
    summary.key_takeaways = ["Point 1", "Point 2"]
    summary.summary_text = "Resumo Tech\nLinha complementar"
    summary.source_article_ids = []
    summary.date = datetime.now(timezone.utc).date()
    summary.created_at = datetime.now(timezone.utc)

    class DummyResult:
        def __init__(self, scalar_value=None, items=None, rows=None):
            self._scalar_value = scalar_value
            self._items = items or []
            self._rows = rows or []

        def scalar(self):
            return self._scalar_value

        def scalars(self):
            return self

        def all(self):
            return self._rows or self._items

    class DummySession:
        def __init__(self):
            self.calls = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def execute(self, statement):
            self.calls += 1
            if self.calls == 1:
                return DummyResult(scalar_value=1)
            if self.calls == 2:
                return DummyResult(items=[summary])
            if self.calls == 3:
                return DummyResult(scalar_value=0)
            return DummyResult(items=[])

    monkeypatch.setattr(db_engine, "async_session", lambda: DummySession())

    response = client.get("/api/dashboard")
    assert response.status_code == 200
    payload = response.json()
    assert payload["summaries"][0]["bullets"] == ["Point 1", "Point 2"]
    assert payload["summaries"][0]["insight"] == ""
    assert payload["summaries"][0]["summaryText"] == "Resumo Tech\nLinha complementar"
    assert payload["summaries"][0]["sourceUrls"] == []
    assert payload["summaries"][0]["sourceCount"] == 0
    assert payload["recentRuns"] == []


def test_dashboard_returns_source_urls_and_recent_runs(monkeypatch):
    """Dashboard should expose full summary text, ordered sources, and recent pipeline runs."""
    from datetime import date, datetime, timezone
    from unittest.mock import MagicMock
    import db.engine as db_engine

    summary = MagicMock()
    summary.id = 99
    summary.category = "economia-brasil"
    summary.period = "evening"
    summary.key_takeaways = {"bullets": ["Primeiro ponto"], "insight": "Insight principal"}
    summary.summary_text = "Brasil Econômico — Noite\nLinha 1 do corpo\nLinha 2 do corpo"
    summary.source_article_ids = [7, 9, 7]
    summary.date = date(2026, 4, 21)
    summary.created_at = datetime(2026, 4, 21, 1, 8, tzinfo=timezone.utc)

    run = MagicMock()
    run.id = 10
    run.period = "evening"
    run.status = "completed"
    run.articles_collected = 12
    run.summaries_generated = 6
    run.messages_sent = 2
    run.started_at = datetime(2026, 4, 21, 1, 0, tzinfo=timezone.utc)
    run.finished_at = datetime(2026, 4, 21, 1, 3, tzinfo=timezone.utc)

    class DummyResult:
        def __init__(self, scalar_value=None, items=None, rows=None):
            self._scalar_value = scalar_value
            self._items = items or []
            self._rows = rows or []

        def scalar(self):
            return self._scalar_value

        def scalars(self):
            return self

        def all(self):
            return self._rows or self._items

    class DummySession:
        def __init__(self):
            self.calls = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def execute(self, statement):
            self.calls += 1
            if self.calls == 1:
                return DummyResult(scalar_value=2)
            if self.calls == 2:
                return DummyResult(items=[summary])
            if self.calls == 3:
                return DummyResult(scalar_value=1)
            if self.calls == 4:
                return DummyResult(items=[run])
            return DummyResult(rows=[(7, "https://example.com/a"), (9, "https://example.com/b")])

    monkeypatch.setattr(db_engine, "async_session", lambda: DummySession())

    response = client.get("/api/dashboard")
    assert response.status_code == 200

    payload = response.json()
    assert payload["pendingSummaries"] == 1
    assert payload["summaries"][0]["summaryText"] == "Brasil Econômico — Noite\nLinha 1 do corpo\nLinha 2 do corpo"
    assert payload["summaries"][0]["sourceUrls"] == [
        "https://example.com/a",
        "https://example.com/b",
    ]
    assert payload["summaries"][0]["sourceCount"] == 2
    assert payload["recentRuns"][0]["articlesCollected"] == 12
    assert payload["recentRuns"][0]["summariesGenerated"] == 6
    assert payload["recentRuns"][0]["messagesSent"] == 2


def test_manual_pipeline_trigger():
    """Test manual pipeline trigger endpoint."""
    response = client.post("/run-pipeline/morning")
    assert response.status_code == 200
    assert response.json()["status"] == "started"


def test_manual_pipeline_invalid_period():
    """Test pipeline trigger with invalid period."""
    response = client.post("/run-pipeline/invalid")
    assert response.status_code == 400
    assert "error" in response.json()
