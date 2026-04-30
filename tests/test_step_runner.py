"""Tests for scheduler.step_runner — step execution, timeout, and event recording."""
import asyncio

import pytest

from scheduler.step_runner import StepResult, execute_step


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _success_step() -> StepResult:
    return StepResult(
        status="ok",
        message="Step completed",
        metadata={"count": 42},
        payload=["data"],
    )


async def _failing_step() -> StepResult:
    raise ValueError("Something broke")


async def _slow_step() -> StepResult:
    await asyncio.sleep(10)
    return StepResult(status="ok", message="Done")


async def _step_returning_failure() -> StepResult:
    """Step that returns a 'failed' result without raising."""
    return StepResult(
        status="failed",
        message="Validation failed",
        error_log="Missing required field",
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestStepResultContract:
    def test_defaults(self):
        r = StepResult(status="ok", message="done")
        assert r.metadata == {}
        assert r.error_log is None
        assert r.payload is None

    def test_full(self):
        r = StepResult(
            status="failed",
            message="timeout",
            metadata={"t": 300},
            error_log="exceeded",
            payload=None,
        )
        assert r.status == "failed"
        assert r.error_log == "exceeded"


class TestExecuteStepSuccess:
    @pytest.mark.asyncio
    async def test_returns_step_result(self, monkeypatch):
        events: list[tuple] = []

        async def fake_record(run_id, step, status, message=None, metadata=None):
            events.append((run_id, step, status, message))

        monkeypatch.setattr("scheduler.step_runner.record_pipeline_event", fake_record)

        result = await execute_step(1, "fetch_feeds", _success_step)

        assert result.status == "ok"
        assert result.message == "Step completed"
        assert result.metadata == {"count": 42}
        assert result.payload == ["data"]

    @pytest.mark.asyncio
    async def test_records_started_and_ok_events(self, monkeypatch):
        events: list[tuple] = []

        async def fake_record(run_id, step, status, message=None, metadata=None):
            events.append((run_id, step, status))

        monkeypatch.setattr("scheduler.step_runner.record_pipeline_event", fake_record)

        await execute_step(
            99, "my_step", _success_step,
            start_message="Starting!",
        )

        assert events[0] == (99, "my_step", "started")
        assert events[1] == (99, "my_step", "ok")


class TestExecuteStepTimeout:
    @pytest.mark.asyncio
    async def test_timeout_returns_failed(self, monkeypatch):
        events: list[tuple] = []

        async def fake_record(run_id, step, status, message=None, metadata=None):
            events.append((run_id, step, status, message))

        monkeypatch.setattr("scheduler.step_runner.record_pipeline_event", fake_record)

        result = await execute_step(
            1, "slow_op", _slow_step,
            timeout_seconds=0.01,
        )

        assert result.status == "failed"
        assert "Timeout" in result.message
        assert result.error_log is not None
        assert events[-1][2] == "failed"


class TestExecuteStepUnexpectedError:
    @pytest.mark.asyncio
    async def test_exception_returns_failed(self, monkeypatch):
        events: list[tuple] = []

        async def fake_record(run_id, step, status, message=None, metadata=None):
            events.append((run_id, step, status, message))

        monkeypatch.setattr("scheduler.step_runner.record_pipeline_event", fake_record)

        result = await execute_step(1, "broken", _failing_step)

        assert result.status == "failed"
        assert "ValueError" in result.message
        assert result.error_log is not None
        assert events[-1][2] == "failed"


class TestExecuteStepLogicalFailure:
    @pytest.mark.asyncio
    async def test_step_returning_failure_is_recorded(self, monkeypatch):
        events: list[tuple] = []

        async def fake_record(run_id, step, status, message=None, metadata=None):
            events.append((run_id, step, status))

        monkeypatch.setattr("scheduler.step_runner.record_pipeline_event", fake_record)

        result = await execute_step(1, "validate", _step_returning_failure)

        assert result.status == "failed"
        assert result.error_log == "Missing required field"
        # started + failed
        assert events == [(1, "validate", "started"), (1, "validate", "failed")]


class TestPipelineRunLifecycle:
    @pytest.mark.asyncio
    async def test_create_and_finish_pipeline_run(self, tmp_path):
        """Integration test: create, update, finish a pipeline run in real SQLite."""
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from db.models import Base, PipelineEvent, PipelineRun

        db_path = tmp_path / "step_runner.sqlite"
        engine = create_async_engine(f"sqlite+aiosqlite:///{db_path.as_posix()}")

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        session_factory = async_sessionmaker(engine, expire_on_commit=False)

        import scheduler.step_runner as sr

        original_session = sr.async_session
        sr.async_session = session_factory
        try:
            run = await sr.create_pipeline_run("morning")
            assert run.id is not None
            assert run.status == "running"
            assert run.period == "morning"

            await sr.record_pipeline_event(run.id, "fetch_feeds", "ok", "Done", {"n": 5})

            await sr.update_pipeline_run(run.id, "completed", articles_collected=10)

            await sr.finish_pipeline_run(run.id)

            # Verify
            async with session_factory() as session:
                db_run = await session.get(PipelineRun, run.id)
                assert db_run.status == "completed"
                assert db_run.articles_collected == 10
                assert db_run.finished_at is not None

                from sqlalchemy import select

                event = await session.scalar(
                    select(PipelineEvent).where(PipelineEvent.run_id == run.id)
                )
                assert event.step == "fetch_feeds"
                assert event.status == "ok"
                assert event.event_metadata == {"n": 5}
        finally:
            sr.async_session = original_session
            await engine.dispose()
