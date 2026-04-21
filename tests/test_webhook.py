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
    summary.category = "tech"
    summary.period = "morning"
    summary.key_takeaways = ["Point 1", "Point 2"]
    summary.created_at = datetime.now(timezone.utc)

    class DummyResult:
        def __init__(self, scalar_value=None, items=None):
            self._scalar_value = scalar_value
            self._items = items or []

        def scalar(self):
            return self._scalar_value

        def scalars(self):
            return self

        def all(self):
            return self._items

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
            return DummyResult(items=[summary])

    monkeypatch.setattr(db_engine, "async_session", lambda: DummySession())

    response = client.get("/api/dashboard")
    assert response.status_code == 200
    payload = response.json()
    assert payload["summaries"][0]["bullets"] == ["Point 1", "Point 2"]
    assert payload["summaries"][0]["insight"] == ""


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
