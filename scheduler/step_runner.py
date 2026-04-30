"""Pipeline step runner — common contract for pipeline steps.

Each pipeline step is an async callable that returns a ``StepResult``.
The ``execute_step`` function handles:

* Recording ``PipelineEvent`` records (started → ok/failed).
* Optional timeout via ``asyncio.wait_for``.
* Consistent error logging and classification.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

import structlog

from db.engine import async_session
from db.models import PipelineEvent, PipelineRun

logger = structlog.get_logger()


@dataclass
class StepResult:
    """Outcome of a single pipeline step."""

    status: str  # "ok" or "failed"
    message: str
    metadata: dict[str, Any] = field(default_factory=dict)
    error_log: str | None = None
    payload: Any = None  # Data produced by this step for the next one


# ---------------------------------------------------------------------------
# Step executor
# ---------------------------------------------------------------------------

async def execute_step(
    run_id: int,
    step_name: str,
    step_fn: Callable[[], Awaitable[StepResult]],
    *,
    timeout_seconds: float | None = None,
    start_message: str | None = None,
    start_metadata: dict[str, Any] | None = None,
) -> StepResult:
    """Run *step_fn*, record events, and handle timeout/errors.

    Returns a ``StepResult`` with ``status="ok"`` or ``status="failed"``.
    The caller decides whether to abort the pipeline or continue.
    """
    await record_pipeline_event(
        run_id,
        step_name,
        "started",
        start_message or f"{step_name} iniciado",
        start_metadata or {},
    )

    try:
        coro = step_fn()
        if timeout_seconds is not None:
            result = await asyncio.wait_for(coro, timeout=timeout_seconds)
        else:
            result = await coro

        await record_pipeline_event(
            run_id, step_name, result.status, result.message, result.metadata,
        )
        return result

    except asyncio.TimeoutError:
        msg = f"Timeout in {step_name} (>{timeout_seconds}s)"
        logger.error(f"{step_name} TIMEOUT: >{timeout_seconds}s")
        await record_pipeline_event(run_id, step_name, "failed", msg)
        return StepResult(status="failed", message=msg, error_log=msg)

    except Exception as exc:
        msg = f"{step_name} error: {type(exc).__name__}"
        logger.error(f"{step_name} ERROR: {type(exc).__name__}: {exc}")
        await record_pipeline_event(run_id, step_name, "failed", msg)
        return StepResult(status="failed", message=msg, error_log=msg)


# ---------------------------------------------------------------------------
# Pipeline run lifecycle helpers
# ---------------------------------------------------------------------------

async def create_pipeline_run(period: str) -> PipelineRun:
    from config.time_utils import local_today, utc_now

    async with async_session() as session:
        run = PipelineRun(
            period=period,
            date=local_today(),
            status="running",
            started_at=utc_now(),
        )
        session.add(run)
        await session.commit()
        await session.refresh(run)
        return run


async def record_pipeline_event(
    run_id: int,
    step: str,
    status: str,
    message: str | None = None,
    metadata: dict | None = None,
) -> None:
    async with async_session() as session:
        session.add(
            PipelineEvent(
                run_id=run_id,
                step=step,
                status=status,
                message=message,
                event_metadata=metadata or {},
            )
        )
        await session.commit()


async def update_pipeline_run(
    run_id: int,
    status: str,
    articles_collected: int | None = None,
    summaries_generated: int | None = None,
    messages_sent: int | None = None,
    error_log: str | None = None,
) -> None:
    async with async_session() as session:
        run = await session.get(PipelineRun, run_id)
        if not run:
            return

        run.status = status
        if articles_collected is not None:
            run.articles_collected = articles_collected
        if summaries_generated is not None:
            run.summaries_generated = summaries_generated
        if messages_sent is not None:
            run.messages_sent = messages_sent
        if error_log is not None:
            run.error_log = error_log

        await session.commit()


async def finish_pipeline_run(run_id: int) -> None:
    from config.time_utils import utc_now

    async with async_session() as session:
        run = await session.get(PipelineRun, run_id)
        if run:
            run.finished_at = utc_now()
            await session.commit()


async def alert_admin(message: str) -> None:
    """Send an alert to the admin phone number."""
    from config.settings import settings

    if not settings.admin_phone:
        logger.warning(f"No admin phone configured, would alert: {message}")
        return

    from delivery.whatsapp_sender import send_single_message

    await send_single_message(settings.admin_phone, f"ALERTA NewsBot: {message}")
