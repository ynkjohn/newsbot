import asyncio
import datetime
import subprocess
import sys
from types import SimpleNamespace
from pathlib import Path

import pytest
from config.settings import Settings
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import interactions.command_router as command_router
from db.models import (
    Base,
    DeliveryLog,
    FeedSource,
    NewsArticle,
    PipelineEvent,
    PipelineRun,
    Subscriber,
    Summary,
)


async def _create_sessionmaker(db_path: Path) -> tuple:
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


class _DummyLimiter:
    async def acquire(self) -> None:
        return None


def test_pipeline_hours_list_accepts_four_valid_hours():
    settings = Settings(pipeline_hours="7,12,17,21")

    assert settings.pipeline_hours_list == [7, 12, 17, 21]


@pytest.mark.asyncio
async def test_pipeline_event_records_step_status_and_metadata(tmp_path):
    from interactions.dashboard_data import build_dashboard_payload

    engine, session_factory = await _create_sessionmaker(tmp_path / "pipeline-events.sqlite")
    try:
        async with session_factory() as session:
            run = PipelineRun(
                period="morning",
                date=datetime.date.today(),
                status="running",
                started_at=datetime.datetime.now(datetime.UTC),
            )
            session.add(run)
            await session.flush()
            session.add(
                PipelineEvent(
                    run_id=run.id,
                    step="fetch_feeds",
                    status="ok",
                    message="Coleta finalizada",
                    event_metadata={"entries": 12},
                )
            )
            await session.commit()

            event = await session.scalar(select(PipelineEvent).where(PipelineEvent.run_id == run.id))
            payload = await build_dashboard_payload(session, {"status": "connected", "connected": True})
    finally:
        await engine.dispose()

    assert event is not None
    assert event.step == "fetch_feeds"
    assert event.status == "ok"
    assert event.message == "Coleta finalizada"
    assert event.event_metadata == {"entries": 12}
    assert payload["operation"]["recentRuns"][0]["events"][0]["step"] == "fetch_feeds"
    assert payload["operation"]["recentRuns"][0]["events"][0]["metadata"] == {"entries": 12}


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("7,12,17", "exactly 4"),
        ("7,12,17,21,23", "exactly 4"),
        ("7,12,nope,21", "integers"),
        ("7,12,24,21", "between 0 and 23"),
        ("7,12,-1,21", "between 0 and 23"),
    ],
)
def test_pipeline_hours_list_rejects_invalid_values(value, message):
    settings = Settings(pipeline_hours=value)

    with pytest.raises(ValueError, match=message):
        settings.pipeline_hours_list


def test_whatsapp_bridge_token_has_local_default():
    settings = Settings(_env_file=None)

    assert settings.whatsapp_bridge_token == "newsbot-local-bridge-token"


@pytest.mark.asyncio
async def test_send_whatsapp_message_retries_timeout_without_blocking_sleep(monkeypatch):
    import delivery.whatsapp_sender as whatsapp_sender

    attempts = []
    sleeps = []

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"ok": True}

    class FakeAsyncClient:
        def __init__(self, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, json, headers):
            attempts.append((url, json, headers))
            if len(attempts) == 1:
                raise whatsapp_sender.httpx.TimeoutException("slow bridge")
            return FakeResponse()

    async def fake_sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr(whatsapp_sender.httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(whatsapp_sender.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(whatsapp_sender.settings, "whatsapp_bridge_url", "http://bridge:3000")
    monkeypatch.setattr(whatsapp_sender.settings, "whatsapp_bridge_token", "secret")

    result = await whatsapp_sender._send_whatsapp_message("5511999999999", "oi")

    assert result == {"ok": True}
    assert len(attempts) == 2
    assert sleeps == [1]
    assert attempts[0][1] == {"number": "5511999999999@s.whatsapp.net", "text": "oi"}
    assert attempts[0][2] == {"Authorization": "Bearer secret"}


@pytest.mark.asyncio
async def test_send_whatsapp_message_returns_none_after_retries(monkeypatch):
    import delivery.whatsapp_sender as whatsapp_sender

    attempts = []
    sleeps = []

    class FakeAsyncClient:
        def __init__(self, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, json, headers):
            attempts.append((url, json, headers))
            raise whatsapp_sender.httpx.ConnectError("bridge down")

    async def fake_sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr(whatsapp_sender.httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(whatsapp_sender.asyncio, "sleep", fake_sleep)

    result = await whatsapp_sender._send_whatsapp_message("5511999999999", "oi")

    assert result is None
    assert len(attempts) == 3
    assert sleeps == [1, 5]


@pytest.mark.asyncio
async def test_manual_pipeline_returns_traceable_run_id(monkeypatch):
    from app import app

    async def fake_pipeline(request_id=None, replace_existing_summaries=False):
        return None

    created_tasks = []

    def fake_create_task(coro):
        created_tasks.append(coro)

        class FakeTask:
            def add_done_callback(self, callback):
                return None

        return FakeTask()

    monkeypatch.setattr("routers.pipeline.run_morning_pipeline", fake_pipeline)
    monkeypatch.setattr("routers.pipeline.asyncio.create_task", fake_create_task)
    monkeypatch.setattr("interactions.admin_auth.settings.admin_username", "admin")
    monkeypatch.setattr("interactions.admin_auth.settings.admin_password", "test-password")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/run-pipeline/morning", auth=("admin", "test-password"))

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "started"
    assert payload["period"] == "morning"
    assert isinstance(payload["run_id"], str)
    assert len(payload["run_id"]) >= 8
    assert len(created_tasks) == 1
    assert created_tasks[0].cr_frame.f_locals["request_id"] == payload["run_id"]
    assert created_tasks[0].cr_frame.f_locals["replace_existing_summaries"] is True

    created_tasks[0].close()


@pytest.mark.asyncio
async def test_dashboard_failed_delivery_drilldown(tmp_path):
    from interactions.dashboard_data import build_dashboard_payload

    engine, session_factory = await _create_sessionmaker(tmp_path / "failed-deliveries.sqlite")
    try:
        async with session_factory() as session:
            subscriber = Subscriber(phone_number="5511999999999", active=True)
            summary = Summary(
                category="tech",
                period="evening",
                date=datetime.date.today(),
                summary_text="Resumo",
                key_takeaways={},
                source_article_ids=[],
                model_used="test",
            )
            session.add_all([subscriber, summary])
            await session.flush()
            session.add(
                DeliveryLog(
                    subscriber_id=subscriber.id,
                    summary_id=summary.id,
                    status="failed",
                    error_message="bridge timeout",
                )
            )
            await session.commit()

            payload = await build_dashboard_payload(session, {"status": "connected", "connected": True})
    finally:
        await engine.dispose()

    failed = payload["operation"]["failedDeliveries"]

    assert failed["count"] == 1
    assert failed["items"][0]["subscriber"] == "5511999999999"
    assert failed["items"][0]["summaryId"] == summary.id
    assert failed["items"][0]["errorMessage"] == "bridge timeout"
    assert failed["items"][0]["retryable"] is True


@pytest.mark.asyncio
async def test_dashboard_feed_health_marks_degraded_and_paused(tmp_path):
    from interactions.dashboard_data import build_dashboard_payload

    engine, session_factory = await _create_sessionmaker(tmp_path / "feed-health.sqlite")
    try:
        now = datetime.datetime.now(datetime.UTC)
        async with session_factory() as session:
            session.add_all([
                FeedSource(
                    url="https://example.com/ok.xml",
                    name="OK",
                    category="tech",
                    active=True,
                    consecutive_errors=0,
                    last_fetched_at=now,
                ),
                FeedSource(
                    url="https://example.com/degraded.xml",
                    name="Degraded",
                    category="tech",
                    active=True,
                    consecutive_errors=2,
                    last_error="Timeout",
                    last_fetched_at=now - datetime.timedelta(hours=3),
                ),
                FeedSource(
                    url="https://example.com/paused.xml",
                    name="Paused",
                    category="tech",
                    active=False,
                    consecutive_errors=0,
                ),
            ])
            await session.commit()

            payload = await build_dashboard_payload(session, {"status": "connected", "connected": True})
    finally:
        await engine.dispose()

    states = {feed["name"]: feed["state"] for feed in payload["operation"]["feedHealth"]}

    assert states["OK"] == "healthy"
    assert states["Degraded"] == "degraded"
    assert states["Paused"] == "paused"


@pytest.mark.asyncio
async def test_dashboard_today_summary_count_counts_only_today(tmp_path):
    from interactions.dashboard_data import build_dashboard_payload

    engine, session_factory = await _create_sessionmaker(tmp_path / "dashboard.sqlite")
    try:
        today = datetime.date.today()
        yesterday = today - datetime.timedelta(days=1)

        async with session_factory() as session:
            session.add_all([
                Summary(
                    category="tech",
                    period="morning",
                    date=today,
                    summary_text="Hoje",
                    key_takeaways={},
                    source_article_ids=[],
                    model_used="test",
                    sent_at=datetime.datetime.now(datetime.UTC),
                ),
                Summary(
                    category="tech",
                    period="morning",
                    date=yesterday,
                    summary_text="Ontem",
                    key_takeaways={},
                    source_article_ids=[],
                    model_used="test",
                    sent_at=datetime.datetime.now(datetime.UTC),
                ),
            ])
            await session.commit()

            payload = await build_dashboard_payload(session, {"status": "connected", "connected": True})
    finally:
        await engine.dispose()

    assert payload["operation"]["todaySummaryCount"] == 1
    assert payload["operation"]["readingWindowSummaryCount"] == 2
    assert payload["operation"]["healthBreakdown"][-1] == {
        "label": "Sem penalidades operacionais",
        "impact": 0,
    }


def test_parse_message_preserves_unknown_news_command():
    assert command_router.parse_message("!pis") == ("command", "!pis")


def test_help_text_explains_headline_drilldown_commands():
    import interactions.command_handlers as command_handlers

    response = command_handlers.HELP_TEXT

    assert "manchetes curtas por editoria" in response
    assert "comando que aparece no fim da manchete" in response
    assert "!pis" in response
    assert "!start" in response


@pytest.mark.asyncio
async def test_unknown_news_command_resolves_to_drilldown(monkeypatch):
    import interactions.command_handlers as command_handlers

    called_with: list[str] = []

    async def fake_build_drilldown_response_for_command(command: str) -> str | None:
        called_with.append(command)
        return "Detalhes sobre PIS"

    monkeypatch.setattr(
        command_handlers,
        "build_drilldown_response_for_command",
        fake_build_drilldown_response_for_command,
    )

    response = await command_handlers.handle_command("!pis", "5511999999999")

    assert response == "Detalhes sobre PIS"
    assert called_with == ["!pis"]


@pytest.mark.asyncio
async def test_build_drilldown_response_for_command_reads_summary_items(tmp_path, monkeypatch):
    import interactions.drilldown_handler as drilldown_handler

    engine, sessionmaker = await _create_sessionmaker(tmp_path / "drilldown.db")
    monkeypatch.setattr(drilldown_handler, "async_session", sessionmaker)

    async with sessionmaker() as session:
        summary = Summary(
            category="economia-brasil",
            period="morning",
            date=datetime.date.today(),
            summary_text="Resumo",
            key_takeaways={
                "items": [
                    {
                        "title": "PIS/Pasep tem novo calendário",
                        "what_happened": "O governo detalhou as datas de pagamento.",
                        "why_it_matters": "A medida afeta trabalhadores que aguardam o benefício.",
                        "watchlist": "Acompanhar o calendário da Caixa.",
                        "command_hint": "!pis",
                    }
                ]
            },
            source_article_ids=[1],
            model_used="model",
            created_at=datetime.datetime.now(datetime.timezone.utc),
        )
        session.add(summary)
        await session.commit()

    response = await drilldown_handler.build_drilldown_response_for_command("!PIS agora")

    assert response is not None
    assert "PIS/Pasep tem novo calendário" in response
    assert "O governo detalhou as datas de pagamento." in response
    assert "A medida afeta trabalhadores que aguardam o benefício." in response
    assert "Acompanhar o calendário da Caixa." in response
    assert "Próximo ponto" in response
    assert "O que aconteceu" in response
    assert "Por que importa" in response
    assert "Fique de olho" not in response
    assert "Para acompanhar" not in response

    await engine.dispose()


@pytest.mark.asyncio
async def test_build_drilldown_response_includes_source_article_titles(tmp_path, monkeypatch):
    import interactions.drilldown_handler as drilldown_handler

    engine, sessionmaker = await _create_sessionmaker(tmp_path / "drilldown_sources.db")
    monkeypatch.setattr(drilldown_handler, "async_session", sessionmaker)
    monkeypatch.setattr(
        drilldown_handler,
        "get_llm_client",
        lambda: (_ for _ in ()).throw(RuntimeError("LLM unavailable in test")),
    )

    async with sessionmaker() as session:
        source = FeedSource(
            url="https://g1.example/rss",
            name="G1 Política",
            category="politica-brasil",
        )
        session.add(source)
        await session.flush()
        article = NewsArticle(
            source_id=source.id,
            url="https://g1.example/dosimetria",
            title="PL da Dosimetria: Câmara rejeita veto de Lula e decisão segue para o Senado",
            raw_content="Texto da notícia",
            category="politica-brasil",
            published_at=datetime.datetime.now(datetime.timezone.utc),
            processed=False,
            content_hash="hash-dosimetria",
        )
        session.add(article)
        await session.flush()
        summary = Summary(
            category="politica-brasil",
            period="afternoon",
            date=datetime.date.today(),
            summary_text="Resumo",
            key_takeaways={
                "items": [
                    {
                        "title": "Congresso derruba veto ao PL da Dosimetria",
                        "what_happened": "Câmara e Senado derrubaram o veto presidencial.",
                        "why_it_matters": "A decisão muda o tratamento penal dos atos de 8 de janeiro.",
                        "watchlist": "Acompanhar eventual contestação no STF.",
                        "source_article_ids": [article.id],
                        "command_hint": "!pl-dosimetria",
                    }
                ]
            },
            source_article_ids=[article.id],
            model_used="model",
            created_at=datetime.datetime.now(datetime.timezone.utc),
        )
        session.add(summary)
        await session.commit()

    response = await drilldown_handler.build_drilldown_response_for_command("!pl-dosimetria")

    assert response is not None
    assert "Base usada" in response
    assert "G1 Política" in response
    assert "PL da Dosimetria: Câmara rejeita veto" in response

    await engine.dispose()


@pytest.mark.asyncio
async def test_build_drilldown_response_uses_llm_with_source_articles(tmp_path, monkeypatch):
    import interactions.drilldown_handler as drilldown_handler

    engine, sessionmaker = await _create_sessionmaker(tmp_path / "drilldown_llm.db")
    monkeypatch.setattr(drilldown_handler, "async_session", sessionmaker)
    calls: list[dict[str, str | int]] = []

    class FakeLLM:
        async def chat_async_with_usage(self, system_prompt, user_prompt, max_tokens):
            calls.append(
                {
                    "system_prompt": system_prompt,
                    "user_prompt": user_prompt,
                    "max_tokens": max_tokens,
                }
            )
            return SimpleNamespace(
                content=(
                    "*Congresso derruba veto ao PL da Dosimetria*\n\n"
                    "Contexto: a votação reabriu a disputa sobre penas dos atos de 8 de janeiro.\n\n"
                    "O que muda: o texto pode reduzir penas, mas ainda depende de contestação judicial.\n\n"
                    "Base usada: G1 Política."
                ),
                usage=None,
            )

    monkeypatch.setattr(drilldown_handler, "get_llm_client", lambda: FakeLLM())

    async with sessionmaker() as session:
        source = FeedSource(
            url="https://g1.example/rss",
            name="G1 Política",
            category="politica-brasil",
        )
        session.add(source)
        await session.flush()
        article = NewsArticle(
            source_id=source.id,
            url="https://g1.example/dosimetria-rich",
            title="Congresso retoma revisão de penas e caso pode chegar ao STF",
            raw_content=(
                "Câmara e Senado derrubaram o veto ao projeto. "
                "O texto reduz penas ligadas aos atos de 8 de janeiro, "
                "mas a aplicação pode ser questionada no Supremo Tribunal Federal."
            ),
            category="politica-brasil",
            published_at=datetime.datetime.now(datetime.timezone.utc),
            processed=False,
            content_hash="hash-dosimetria-rich",
        )
        session.add(article)
        await session.flush()
        session.add(
            Summary(
                category="politica-brasil",
                period="afternoon",
                date=datetime.date.today(),
                summary_text="Resumo",
                key_takeaways={
                    "items": [
                        {
                            "title": "Congresso derruba veto ao PL da Dosimetria",
                            "what_happened": "Câmara e Senado derrubaram o veto presidencial.",
                            "why_it_matters": "A decisão muda o tratamento penal dos atos de 8 de janeiro.",
                            "watchlist": "Acompanhar eventual contestação no STF.",
                            "source_article_ids": [article.id],
                            "command_hint": "!pl-dosimetria",
                        }
                    ]
                },
                source_article_ids=[article.id],
                model_used="model",
                created_at=datetime.datetime.now(datetime.timezone.utc),
            )
        )
        await session.commit()

    response = await drilldown_handler.build_drilldown_response_for_command("!pl-dosimetria")

    assert response is not None
    assert response.startswith("*Congresso derruba veto")
    assert "Contexto:" in response
    assert calls[0]["max_tokens"] == 1600
    assert "Congresso retoma revisão de penas" in str(calls[0]["user_prompt"])
    assert "Supremo Tribunal Federal" in str(calls[0]["user_prompt"])

    await engine.dispose()


@pytest.mark.asyncio
async def test_build_drilldown_response_falls_back_when_llm_fails(tmp_path, monkeypatch):
    import interactions.drilldown_handler as drilldown_handler

    engine, sessionmaker = await _create_sessionmaker(tmp_path / "drilldown_llm_fallback.db")
    monkeypatch.setattr(drilldown_handler, "async_session", sessionmaker)

    class FailingLLM:
        async def chat_async_with_usage(self, system_prompt, user_prompt, max_tokens):
            raise RuntimeError("provider timeout")

    monkeypatch.setattr(drilldown_handler, "get_llm_client", lambda: FailingLLM())

    async with sessionmaker() as session:
        source = FeedSource(
            url="https://g1.example/rss",
            name="G1 Política",
            category="politica-brasil",
        )
        session.add(source)
        await session.flush()
        article = NewsArticle(
            source_id=source.id,
            url="https://g1.example/dosimetria-fallback",
            title="PL da Dosimetria: decisão segue sob risco judicial",
            raw_content="Texto da notícia",
            category="politica-brasil",
            published_at=datetime.datetime.now(datetime.timezone.utc),
            processed=False,
            content_hash="hash-dosimetria-fallback",
        )
        session.add(article)
        await session.flush()
        session.add(
            Summary(
                category="politica-brasil",
                period="afternoon",
                date=datetime.date.today(),
                summary_text="Resumo",
                key_takeaways={
                    "items": [
                        {
                            "title": "Congresso derruba veto ao PL da Dosimetria",
                            "what_happened": "Câmara e Senado derrubaram o veto presidencial.",
                            "why_it_matters": "A decisão muda o tratamento penal dos atos de 8 de janeiro.",
                            "watchlist": "Acompanhar eventual contestação no STF.",
                            "source_article_ids": [article.id],
                            "command_hint": "!pl-dosimetria",
                        }
                    ]
                },
                source_article_ids=[article.id],
                model_used="model",
                created_at=datetime.datetime.now(datetime.timezone.utc),
            )
        )
        await session.commit()

    response = await drilldown_handler.build_drilldown_response_for_command("!pl-dosimetria")

    assert response is not None
    assert "O que aconteceu: Câmara e Senado derrubaram o veto presidencial." in response
    assert "Base usada" in response
    assert "PL da Dosimetria: decisão segue sob risco judicial" in response

    await engine.dispose()


@pytest.mark.asyncio
async def test_build_drilldown_response_ignores_ambiguous_command_hint(tmp_path, monkeypatch):
    import interactions.drilldown_handler as drilldown_handler

    engine, sessionmaker = await _create_sessionmaker(tmp_path / "drilldown_ambiguous.db")
    monkeypatch.setattr(drilldown_handler, "async_session", sessionmaker)

    async with sessionmaker() as session:
        first_summary = Summary(
            category="economia-brasil",
            period="morning",
            date=datetime.date.today(),
            summary_text="Resumo 1",
            key_takeaways={
                "items": [
                    {
                        "title": "PIS/Pasep tem novo calendário",
                        "what_happened": "O governo detalhou as datas de pagamento.",
                        "why_it_matters": "A medida afeta trabalhadores que aguardam o benefício.",
                        "watchlist": "Acompanhar o calendário da Caixa.",
                        "command_hint": "!pis",
                    }
                ]
            },
            source_article_ids=[1],
            model_used="model",
            created_at=datetime.datetime.now(datetime.timezone.utc),
        )
        second_summary = Summary(
            category="economia-brasil",
            period="evening",
            date=datetime.date.today(),
            summary_text="Resumo 2",
            key_takeaways={
                "items": [
                    {
                        "title": "PIS/Pasep abre consulta diferente",
                        "what_happened": "Outro informe usou o mesmo comando curto.",
                        "why_it_matters": "Responder qualquer um dos dois confundiria o usuário.",
                        "watchlist": "Aguardar um comando mais específico.",
                        "command_hint": "!pis",
                    }
                ]
            },
            source_article_ids=[2],
            model_used="model",
            created_at=datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=1),
        )
        session.add_all([first_summary, second_summary])
        await session.commit()

    response = await drilldown_handler.build_drilldown_response_for_command("!pis")

    assert response is None

    await engine.dispose()



def test_render_item_drilldown_preserves_watchlist_acronym():
    from interactions.drilldown_handler import _render_item_drilldown

    response = _render_item_drilldown(
        {
            "title": "Inflação no radar",
            "what_happened": "Dados prévios apontam pressão de preços.",
            "why_it_matters": "A leitura pode afetar juros.",
            "watchlist": "IPCA de março e comunicação do Banco Central.",
        }
    )

    assert response is not None
    assert "IPCA de março" in response
    assert "iPCA" not in response



def test_normalize_group_question_removes_numeric_mentions():
    import interactions.question_handler as question_handler

    cleaned = question_handler._normalize_group_question(
        "@229373315686421 qual e a principal noticia da noite?"
    )
    assert cleaned == "qual e a principal noticia da noite?"


def test_single_headline_question_detection():
    import interactions.question_handler as question_handler

    assert (
        question_handler._is_single_headline_question(
            "qual e a principal noticia da noite?"
        )
        is True
    )
    assert question_handler._is_single_headline_question("quais foram os destaques?") is False


@pytest.mark.asyncio
async def test_ignored_dm_does_not_create_subscriber(tmp_path, monkeypatch):
    import interactions.subscriber_manager as subscriber_manager
    import interactions.webhook_handler as webhook_handler

    engine, sessionmaker = await _create_sessionmaker(tmp_path / "ignored.db")
    monkeypatch.setattr(webhook_handler, "async_session", sessionmaker)
    monkeypatch.setattr(subscriber_manager, "async_session", sessionmaker)

    await webhook_handler.handle_incoming_message("5511999999999@s.whatsapp.net", "aa")

    async with sessionmaker() as session:
        subscribers = (await session.execute(select(Subscriber))).scalars().all()
        assert subscribers == []

    await engine.dispose()


@pytest.mark.asyncio
async def test_group_question_does_not_create_subscriber(tmp_path, monkeypatch):
    import delivery.whatsapp_sender as whatsapp_sender
    import interactions.subscriber_manager as subscriber_manager
    import interactions.webhook_handler as webhook_handler

    engine, sessionmaker = await _create_sessionmaker(tmp_path / "group.db")
    monkeypatch.setattr(webhook_handler, "async_session", sessionmaker)
    monkeypatch.setattr(subscriber_manager, "async_session", sessionmaker)

    async def fake_handle_question(phone_number: str, question: str, is_group: bool = False) -> str:
        assert is_group is True
        return "ok"

    async def fake_send_single_message(phone_number: str, text: str) -> str:
        return "sent"

    monkeypatch.setattr(webhook_handler, "handle_question", fake_handle_question)
    monkeypatch.setattr(whatsapp_sender, "send_single_message", fake_send_single_message)

    await webhook_handler.handle_incoming_message("120363040996567349@g.us", "Qual o resumo de hoje?")

    async with sessionmaker() as session:
        subscribers = (await session.execute(select(Subscriber))).scalars().all()
        assert subscribers == []

    await engine.dispose()


@pytest.mark.asyncio
async def test_group_subscribe_command_uses_full_group_jid(tmp_path, monkeypatch):
    import delivery.whatsapp_sender as whatsapp_sender
    import interactions.subscriber_manager as subscriber_manager
    import interactions.webhook_handler as webhook_handler

    engine, sessionmaker = await _create_sessionmaker(tmp_path / "group_subscribe.db")
    monkeypatch.setattr(webhook_handler, "async_session", sessionmaker)
    monkeypatch.setattr(subscriber_manager, "async_session", sessionmaker)

    async def fake_send_single_message(phone_number: str, text: str) -> str:
        return "sent"

    monkeypatch.setattr(whatsapp_sender, "send_single_message", fake_send_single_message)

    await webhook_handler.handle_incoming_message("120363040996567349@g.us", "!start")

    async with sessionmaker() as session:
        subscribers = (await session.execute(select(Subscriber))).scalars().all()
        assert len(subscribers) == 1
        assert subscribers[0].phone_number == "120363040996567349@g.us"
        assert subscribers[0].active is True

    await engine.dispose()


@pytest.mark.asyncio
async def test_retrieve_context_uses_last_24_hours_across_date_boundary(tmp_path, monkeypatch):
    import interactions.question_handler as question_handler

    engine, sessionmaker = await _create_sessionmaker(tmp_path / "context.db")
    monkeypatch.setattr(question_handler, "async_session", sessionmaker)

    recent_created_at = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=2)
    yesterday = (recent_created_at - datetime.timedelta(hours=1)).date()

    async with sessionmaker() as session:
        summary = Summary(
            category="tech",
            period="evening",
            date=yesterday,
            summary_text="Resumo recente sobre IA",
            key_takeaways={"bullets": ["IA"], "insight": "x"},
            source_article_ids=[],
            model_used="model",
            created_at=recent_created_at,
        )
        session.add(summary)
        await session.commit()

    context = await question_handler._retrieve_context("O que aconteceu com IA?")
    assert "Resumo recente sobre IA" in context

    await engine.dispose()


@pytest.mark.asyncio
async def test_send_digest_does_not_mark_summary_sent_on_total_failure(tmp_path, monkeypatch):
    import delivery.whatsapp_sender as whatsapp_sender

    engine, sessionmaker = await _create_sessionmaker(tmp_path / "send_fail.db")
    monkeypatch.setattr(whatsapp_sender, "async_session", sessionmaker)
    monkeypatch.setattr(whatsapp_sender, "rate_limiter", _DummyLimiter())
    monkeypatch.setattr(
        whatsapp_sender,
        "filter_summaries_by_preferences",
        lambda summaries, preferences: summaries,
    )
    monkeypatch.setattr(whatsapp_sender, "split_message", lambda text: ["parte 1"])
    async def fake_send_failure(phone: str, text: str) -> None:
        return None

    monkeypatch.setattr(whatsapp_sender, "_send_whatsapp_message", fake_send_failure)

    async with sessionmaker() as session:
        subscriber = Subscriber(phone_number="5511999999999", active=True)
        summary = Summary(
            category="tech",
            period="morning",
            date=datetime.date.today(),
            summary_text="Resumo",
            key_takeaways={"bullets": ["a", "b", "c"], "insight": "x"},
            source_article_ids=[1],
            model_used="model",
        )
        session.add_all([subscriber, summary])
        await session.commit()
        await session.refresh(subscriber)
        await session.refresh(summary)

    sent = await whatsapp_sender.send_digest([subscriber], [summary], "morning")
    assert sent == 0

    async with sessionmaker() as session:
        db_summary = await session.get(Summary, summary.id)
        logs = (await session.execute(select(DeliveryLog))).scalars().all()
        assert db_summary.sent_at is None
        assert len(logs) == 1
        assert logs[0].status == "failed"

    await engine.dispose()


@pytest.mark.asyncio
async def test_send_digest_logs_once_per_summary_on_multipart_success(tmp_path, monkeypatch):
    import delivery.whatsapp_sender as whatsapp_sender

    engine, sessionmaker = await _create_sessionmaker(tmp_path / "send_success.db")
    monkeypatch.setattr(whatsapp_sender, "async_session", sessionmaker)
    monkeypatch.setattr(whatsapp_sender, "rate_limiter", _DummyLimiter())
    monkeypatch.setattr(
        whatsapp_sender,
        "filter_summaries_by_preferences",
        lambda summaries, preferences: summaries,
    )
    monkeypatch.setattr(whatsapp_sender, "split_message", lambda text: ["parte 1", "parte 2"])
    async def fake_send_success(phone: str, text: str) -> dict:
        return {"success": True}

    monkeypatch.setattr(whatsapp_sender, "_send_whatsapp_message", fake_send_success)

    async with sessionmaker() as session:
        subscriber = Subscriber(phone_number="5511999999999", active=True)
        summary = Summary(
            category="tech",
            period="morning",
            date=datetime.date.today(),
            summary_text="Resumo",
            key_takeaways={"bullets": ["a", "b", "c"], "insight": "x"},
            source_article_ids=[1],
            model_used="model",
        )
        session.add_all([subscriber, summary])
        await session.commit()
        await session.refresh(subscriber)
        await session.refresh(summary)

    sent = await whatsapp_sender.send_digest([subscriber], [summary], "morning")
    assert sent == 1

    async with sessionmaker() as session:
        logs = (await session.execute(select(DeliveryLog))).scalars().all()
        db_summary = await session.get(Summary, summary.id)
        assert len(logs) == 1
        assert logs[0].status == "sent"
        assert db_summary.sent_at is not None

    await engine.dispose()


@pytest.mark.asyncio
async def test_send_digest_prefers_group_jid_over_legacy_plain_id(tmp_path, monkeypatch):
    import delivery.whatsapp_sender as whatsapp_sender

    engine, sessionmaker = await _create_sessionmaker(tmp_path / "group_send.db")
    monkeypatch.setattr(whatsapp_sender, "async_session", sessionmaker)
    monkeypatch.setattr(whatsapp_sender, "rate_limiter", _DummyLimiter())
    monkeypatch.setattr(
        whatsapp_sender,
        "filter_summaries_by_preferences",
        lambda summaries, preferences: summaries,
    )
    monkeypatch.setattr(whatsapp_sender, "split_message", lambda text: ["parte 1"])

    sent_to: list[str] = []

    async def fake_send(phone: str, text: str) -> dict:
        sent_to.append(phone)
        return {"success": True}

    monkeypatch.setattr(whatsapp_sender, "_send_whatsapp_message", fake_send)

    async with sessionmaker() as session:
        group_subscriber = Subscriber(phone_number="120363040996567349@g.us", active=True)
        legacy_subscriber = Subscriber(phone_number="120363040996567349", active=True)
        summary = Summary(
            category="tech",
            period="morning",
            date=datetime.date.today(),
            summary_text="Resumo",
            key_takeaways={"bullets": ["a", "b", "c"], "insight": "x"},
            source_article_ids=[1],
            model_used="model",
        )
        session.add_all([group_subscriber, legacy_subscriber, summary])
        await session.commit()
        await session.refresh(group_subscriber)
        await session.refresh(legacy_subscriber)
        await session.refresh(summary)

    sent = await whatsapp_sender.send_digest(
        [legacy_subscriber, group_subscriber],
        [summary],
        "morning",
    )

    assert sent == 1
    assert sent_to == ["120363040996567349@g.us"]

    await engine.dispose()


def test_alembic_delivery_log_matches_orm_model(tmp_path):
    root = Path(__file__).resolve().parent.parent
    db_path = tmp_path / "alembic.db"
    sync_url = f"sqlite:///{db_path.as_posix()}"
    subprocess.run(
        [
            sys.executable,
            "-c",
            "import platform, runpy, sys; "
            "platform.machine = lambda: 'AMD64'; "
            "sys.argv = ['alembic', '-c', sys.argv[1], '-x', sys.argv[2], 'upgrade', 'head']; "
            "runpy.run_module('alembic', run_name='__main__')",
            str(root / "alembic.ini"),
            f"sqlalchemy.url={sync_url}",
        ],
        cwd=root,
        check=True,
    )

    async def _insert_delivery_log() -> None:
        engine = create_async_engine(f"sqlite+aiosqlite:///{db_path.as_posix()}")
        sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
        async with sessionmaker() as session:
            subscriber = Subscriber(phone_number="5511999999999", active=True)
            summary = Summary(
                category="tech",
                period="morning",
                date=datetime.date.today(),
                summary_text="Resumo",
                key_takeaways={"bullets": ["a", "b", "c"], "insight": "x"},
                source_article_ids=[1],
                model_used="model",
            )
            session.add_all([subscriber, summary])
            await session.commit()
            await session.refresh(subscriber)
            await session.refresh(summary)

            session.add(
                DeliveryLog(
                    subscriber_id=subscriber.id,
                    summary_id=summary.id,
                    status="sent",
                )
            )
            await session.commit()
        await engine.dispose()

    asyncio.run(_insert_delivery_log())
