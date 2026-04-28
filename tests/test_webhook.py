"""Tests for webhook validation and admin-protected endpoints."""
import base64
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

import app as app_module
from app import WhatsAppWebhookPayload, app
from config.settings import settings

client = TestClient(app)
TEST_WEBHOOK_TOKEN = "test-webhook-token-12345"


@pytest.fixture(autouse=True)
def set_webhook_token(monkeypatch):
    monkeypatch.setattr(settings, "whatsapp_bridge_token", TEST_WEBHOOK_TOKEN)


def _get_webhook_headers():
    return {"Authorization": f"Bearer {TEST_WEBHOOK_TOKEN}"}


def _get_admin_headers():
    encoded = base64.b64encode(f"{settings.admin_username}:{settings.admin_password}".encode("utf-8")).decode("ascii")
    return {"Authorization": f"Basic {encoded}"}


def test_webhook_valid_payload():
    payload = {
        "key": {"remoteJid": "551234567890@s.whatsapp.net"},
        "message": {"conversation": "Hello!"},
    }
    response = client.post("/webhook/whatsapp", json=payload, headers=_get_webhook_headers())
    assert response.status_code == 200


def test_webhook_invalid_json():
    response = client.post(
        "/webhook/whatsapp",
        content="not json",
        headers={"Content-Type": "application/json", **_get_webhook_headers()},
    )
    assert response.status_code == 400
    assert "error" in response.json()


def test_webhook_missing_key():
    payload = {"message": {"conversation": "Hello!"}}
    response = client.post("/webhook/whatsapp", json=payload, headers=_get_webhook_headers())
    assert response.status_code == 422
    assert "error" in response.json()


def test_webhook_missing_message():
    payload = {"key": {"remoteJid": "551234567890@s.whatsapp.net"}}
    response = client.post("/webhook/whatsapp", json=payload, headers=_get_webhook_headers())
    assert response.status_code == 422


def test_webhook_empty_message():
    payload = {
        "key": {"remoteJid": "551234567890@s.whatsapp.net"},
        "message": {"conversation": "   "},
    }
    response = client.post("/webhook/whatsapp", json=payload, headers=_get_webhook_headers())
    assert response.status_code in [400, 422]


def test_webhook_missing_remote_jid():
    payload = {"key": {}, "message": {"conversation": "Hello!"}}
    response = client.post("/webhook/whatsapp", json=payload, headers=_get_webhook_headers())
    assert response.status_code == 422


def test_webhook_missing_authorization_header():
    payload = {
        "key": {"remoteJid": "551234567890@s.whatsapp.net"},
        "message": {"conversation": "Hello!"},
    }
    response = client.post("/webhook/whatsapp", json=payload)
    assert response.status_code == 401
    assert "Unauthorized" in response.json()["error"]


def test_webhook_invalid_token():
    payload = {
        "key": {"remoteJid": "551234567890@s.whatsapp.net"},
        "message": {"conversation": "Hello!"},
    }
    response = client.post(
        "/webhook/whatsapp",
        json=payload,
        headers={"Authorization": "Bearer invalid-token"},
    )
    assert response.status_code == 403
    assert "Forbidden" in response.json()["error"]


def test_pydantic_webhook_validation():
    valid_payload = {
        "key": {"remoteJid": "551234567890@s.whatsapp.net"},
        "message": {"conversation": "Hello"},
    }
    model = WhatsAppWebhookPayload(**valid_payload)
    assert model.key.remoteJid == "551234567890@s.whatsapp.net"
    assert model.message.conversation == "Hello"


def test_pydantic_webhook_validation_empty_message():
    invalid_payload = {
        "key": {"remoteJid": "551234567890@s.whatsapp.net"},
        "message": {"conversation": ""},
    }
    with pytest.raises(ValidationError):
        WhatsAppWebhookPayload(**invalid_payload)


def test_pydantic_webhook_validation_empty_jid():
    invalid_payload = {
        "key": {"remoteJid": ""},
        "message": {"conversation": "Hello"},
    }
    with pytest.raises(ValidationError):
        WhatsAppWebhookPayload(**invalid_payload)


def test_health_endpoint():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_dashboard_requires_admin_auth():
    response = client.get("/api/dashboard")
    assert response.status_code == 401


def test_dashboard_page_serves_intelligence_ui():
    response = client.get("/dashboard", headers=_get_admin_headers())
    assert response.status_code == 200
    assert "NewsBot — Intelligence" in response.text
    assert 'href="/static/dashboard.css' in response.text
    assert 'src="/static/dashboard-extra.js' in response.text


def test_dashboard_accepts_list_key_takeaways(monkeypatch):
    from datetime import datetime, timezone
    from unittest.mock import MagicMock

    summary = MagicMock()
    summary.id = 1
    summary.category = "tech"
    summary.period = "morning"
    summary.key_takeaways = ["Point 1", "Point 2"]
    summary.summary_text = "Resumo Tech\n\nLinha complementar"
    summary.source_article_ids = []
    summary.date = datetime.now(timezone.utc).date()
    summary.created_at = datetime.now(timezone.utc)
    summary.sent_at = None
    summary.model_used = "test-model"

    class DummyResult:
        def __init__(self, items=None, rows=None):
            self._items = items or []
            self._rows = rows or []

        def scalars(self):
            return self

        def all(self):
            return self._rows or self._items

    class DummySession:
        def __init__(self):
            self.scalar_calls = 0
            self.execute_calls = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def scalar(self, statement):
            self.scalar_calls += 1
            return [1, 0, 0][self.scalar_calls - 1]

        async def execute(self, statement):
            self.execute_calls += 1
            if self.execute_calls == 1:
                return DummyResult(items=[summary])
            if self.execute_calls == 2:
                return DummyResult(items=[])
            if self.execute_calls == 3:
                return DummyResult(items=[])
            if self.execute_calls == 4:
                return DummyResult(items=[])
            return DummyResult(rows=[])

    monkeypatch.setattr(app_module, "async_session", lambda: DummySession())
    monkeypatch.setattr(app_module, "fetch_whatsapp_status", AsyncMock(return_value={"status": "connected", "connected": True}))

    response = client.get("/api/dashboard", headers=_get_admin_headers())
    assert response.status_code == 200
    payload = response.json()
    card = payload["reading"]["cards"][0]
    assert card["bullets"] == ["Point 1", "Point 2"]
    assert card["insight"] == ""
    assert card["sourceUrls"] == []
    assert card["sourceCount"] == 0
    assert card["bodySections"][0]["content"] == "Linha complementar"


def test_dashboard_returns_source_urls_and_recent_runs(monkeypatch):
    from datetime import date, datetime, timezone
    from unittest.mock import MagicMock

    summary = MagicMock()
    summary.id = 99
    summary.category = "economia-brasil"
    summary.period = "evening"
    summary.key_takeaways = {
        "version": 2,
        "header": "💵 Economia Nacional — Noite",
        "bullets": ["Primeiro ponto relevante", "Segundo ponto relevante", "Terceiro ponto relevante"],
        "insight": "Insight principal com contexto suficiente para exibição.",
        "sections": [
            {"key": "o_que_mudou", "title": "O que mudou", "content": "Linha 1 do corpo"},
            {"key": "por_que_importa", "title": "Por que importa", "content": "Linha 2 do corpo"},
        ],
    }
    summary.summary_text = "💵 Economia Nacional — Noite\n\nLinha 1 do corpo\n\nLinha 2 do corpo"
    summary.source_article_ids = [7, 9, 7]
    summary.date = date(2026, 4, 21)
    summary.created_at = datetime(2026, 4, 21, 1, 8, tzinfo=timezone.utc)
    summary.sent_at = None
    summary.model_used = "test-model"

    run = MagicMock()
    run.id = 10
    run.period = "evening"
    run.status = "completed"
    run.articles_collected = 12
    run.summaries_generated = 6
    run.messages_sent = 2
    run.error_log = ""
    run.started_at = datetime(2026, 4, 21, 1, 0, tzinfo=timezone.utc)
    run.finished_at = datetime(2026, 4, 21, 1, 3, tzinfo=timezone.utc)

    class DummyResult:
        def __init__(self, items=None, rows=None):
            self._items = items or []
            self._rows = rows or []

        def scalars(self):
            return self

        def all(self):
            return self._rows or self._items

    class DummySession:
        def __init__(self):
            self.scalar_calls = 0
            self.execute_calls = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def scalar(self, statement):
            self.scalar_calls += 1
            return [2, 1, 0][self.scalar_calls - 1]

        async def execute(self, statement):
            self.execute_calls += 1
            if self.execute_calls == 1:
                return DummyResult(items=[summary])
            if self.execute_calls == 2:
                return DummyResult(items=[])
            if self.execute_calls == 3:
                return DummyResult(items=[run])
            if self.execute_calls == 4:
                return DummyResult(items=[run])
            return DummyResult(rows=[(7, "https://example.com/a"), (9, "https://example.com/b")])

    monkeypatch.setattr(app_module, "async_session", lambda: DummySession())
    monkeypatch.setattr(app_module, "fetch_whatsapp_status", AsyncMock(return_value={"status": "connected", "connected": True}))

    response = client.get("/api/dashboard", headers=_get_admin_headers())
    assert response.status_code == 200

    payload = response.json()
    assert payload["operation"]["pendingSummaryCount"] == 1
    assert payload["reading"]["cards"][0]["sourceUrls"] == [
        "https://example.com/a",
        "https://example.com/b",
    ]
    assert payload["reading"]["cards"][0]["sourceCount"] == 2
    assert payload["operation"]["recentRuns"][0]["articlesCollected"] == 12
    assert payload["operation"]["recentRuns"][0]["summariesGenerated"] == 6
    assert payload["operation"]["recentRuns"][0]["messagesSent"] == 2


def test_manual_pipeline_trigger():
    response = client.post("/run-pipeline/morning", headers=_get_admin_headers())
    assert response.status_code == 200
    assert response.json()["status"] == "started"


def test_manual_pipeline_invalid_period():
    response = client.post("/run-pipeline/invalid", headers=_get_admin_headers())
    assert response.status_code == 400
    assert "error" in response.json()
