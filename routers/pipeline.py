"""Pipeline routes — digest preview, manual trigger, and last-24h action."""

import asyncio
from uuid import uuid4

import structlog
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from config.time_utils import local_now, local_today
from core.periods import VALID_PERIODS_SET
from db.engine import async_session
from interactions.admin_auth import require_admin
from scheduler.jobs import (
    run_afternoon_pipeline,
    run_evening_pipeline,
    run_midday_pipeline,
    run_morning_pipeline,
)

logger = structlog.get_logger()

router = APIRouter()


async def _build_digest_preview(period: str):
    from sqlalchemy import select

    from db.models import Summary
    from delivery.message_formatter import format_digest, split_message

    if period not in VALID_PERIODS_SET:
        return JSONResponse(
            status_code=400,
            content={"error": "period must be 'morning', 'midday', 'afternoon' or 'evening'"},
        )

    today = local_today()
    async with async_session() as session:
        result = await session.execute(
            select(Summary)
            .where(Summary.date == today, Summary.period == period)
            .order_by(Summary.category.asc(), Summary.created_at.asc())
        )
        summaries = result.scalars().all()

    preview_text = format_digest(summaries, today, period)
    parts = split_message(preview_text)
    return {
        "period": period,
        "date": today.isoformat(),
        "generatedAt": local_now().isoformat(),
        "summaryCount": len(summaries),
        "charCount": len(preview_text),
        "partCount": len(parts),
        "text": preview_text,
        "parts": parts,
    }


@router.get("/api/digest-preview/{period}", dependencies=[Depends(require_admin)])
async def digest_preview(period: str):
    return await _build_digest_preview(period)


@router.post("/api/run-pipeline/last-24h", dependencies=[Depends(require_admin)])
async def preview_last_24h_pipeline_action():
    return await _build_digest_preview("morning")


@router.post("/run-pipeline/{period}", dependencies=[Depends(require_admin)])
async def trigger_pipeline(period: str):
    pipeline_map = {
        "morning": run_morning_pipeline,
        "midday": run_midday_pipeline,
        "afternoon": run_afternoon_pipeline,
        "evening": run_evening_pipeline,
    }
    if period not in VALID_PERIODS_SET:
        return JSONResponse(
            status_code=400,
            content={"error": "period must be 'morning', 'midday', 'afternoon' or 'evening'"},
        )

    run_id = uuid4().hex[:12]
    logger.info(f"Manual pipeline run requested: period={period} run_id={run_id}")
    task = asyncio.create_task(
        pipeline_map[period](
            request_id=run_id,
            replace_existing_summaries=True,
        )
    )

    def log_pipeline_result(done_task: asyncio.Task, requested_period: str = period, requested_run_id: str = run_id) -> None:
        try:
            done_task.result()
        except Exception as exc:
            logger.error(
                f"Manual pipeline run failed: period={requested_period} run_id={requested_run_id} error={type(exc).__name__}: {exc}"
            )
        else:
            logger.info(f"Manual pipeline run finished: period={requested_period} run_id={requested_run_id}")

    task.add_done_callback(log_pipeline_result)
    return {
        "status": "started",
        "period": period,
        "run_id": run_id,
        "message": "Pipeline iniciado. Acompanhe a execução nos logs do serviço newsbot.",
    }
