import asyncio
import datetime
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import delivery.whatsapp_sender as whatsapp_sender
import interactions.question_handler as question_handler
import interactions.subscriber_manager as subscriber_manager
import interactions.webhook_handler as webhook_handler
from db.models import Base, DeliveryLog, Subscriber, Summary


async def _create_sessionmaker(db_path: Path) -> tuple:
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


class _DummyLimiter:
    async def acquire(self) -> None:
        return None


def test_normalize_group_question_removes_numeric_mentions():
    cleaned = question_handler._normalize_group_question(
        "@229373315686421 qual e a principal noticia da noite?"
    )
    assert cleaned == "qual e a principal noticia da noite?"


def test_single_headline_question_detection():
    assert (
        question_handler._is_single_headline_question(
            "qual e a principal noticia da noite?"
        )
        is True
    )
    assert question_handler._is_single_headline_question("quais foram os destaques?") is False


@pytest.mark.asyncio
async def test_ignored_dm_does_not_create_subscriber(tmp_path, monkeypatch):
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
    engine, sessionmaker = await _create_sessionmaker(tmp_path / "send_fail.db")
    monkeypatch.setattr(whatsapp_sender, "async_session", sessionmaker)
    monkeypatch.setattr(whatsapp_sender, "rate_limiter", _DummyLimiter())
    monkeypatch.setattr(
        whatsapp_sender,
        "filter_summaries_by_preferences",
        lambda summaries, preferences: summaries,
    )
    monkeypatch.setattr(whatsapp_sender, "split_message", lambda text: ["parte 1"])
    monkeypatch.setattr(whatsapp_sender, "_send_whatsapp_message", lambda phone, text: None)

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
    engine, sessionmaker = await _create_sessionmaker(tmp_path / "send_success.db")
    monkeypatch.setattr(whatsapp_sender, "async_session", sessionmaker)
    monkeypatch.setattr(whatsapp_sender, "rate_limiter", _DummyLimiter())
    monkeypatch.setattr(
        whatsapp_sender,
        "filter_summaries_by_preferences",
        lambda summaries, preferences: summaries,
    )
    monkeypatch.setattr(whatsapp_sender, "split_message", lambda text: ["parte 1", "parte 2"])
    monkeypatch.setattr(
        whatsapp_sender,
        "_send_whatsapp_message",
        lambda phone, text: {"success": True},
    )

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

    def fake_send(phone: str, text: str) -> dict:
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
            "-m",
            "alembic",
            "-c",
            str(root / "alembic.ini"),
            "-x",
            f"sqlalchemy.url={sync_url}",
            "upgrade",
            "head",
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
