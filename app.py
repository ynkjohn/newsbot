"""NewsBot — FastAPI application composition.

This module creates the FastAPI app, registers the scheduler, mounts
static files, and includes all routers.  Business logic lives in the
router and service modules.
"""

from contextlib import asynccontextmanager
from pathlib import Path

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from collector.sources import seed_feeds_if_empty
from config.settings import settings
from db.engine import init_db
from routers.admin_api import router as admin_api_router
from routers.dashboard import router as dashboard_router
from routers.pipeline import router as pipeline_router
from routers.webhook import router as webhook_router
from scheduler.jobs import (
    check_feed_health,
    cleanup_old_articles,
    run_afternoon_pipeline,
    run_evening_pipeline,
    run_midday_pipeline,
    run_morning_pipeline,
)

# Re-export for backward compatibility with existing tests
from schemas.webhook import WhatsAppWebhookPayload  # noqa: F401

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.dev.ConsoleRenderer(),
    ],
)

logger = structlog.get_logger()
scheduler = AsyncIOScheduler(timezone=settings.timezone)
BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting NewsBot...")
    await init_db()
    await seed_feeds_if_empty()

    pipeline_jobs = [
        ("morning", run_morning_pipeline),
        ("midday", run_midday_pipeline),
        ("afternoon", run_afternoon_pipeline),
        ("evening", run_evening_pipeline),
    ]
    for hour, (period, func) in zip(settings.pipeline_hours_list, pipeline_jobs):
        scheduler.add_job(
            func,
            CronTrigger(hour=hour, minute=0),
            id=f"{period}_pipeline",
            max_instances=1,
            misfire_grace_time=900,
        )

    scheduler.add_job(cleanup_old_articles, CronTrigger(hour=0, minute=30), id="cleanup", max_instances=1)
    scheduler.add_job(check_feed_health, CronTrigger(hour="*/6"), id="feed_health", max_instances=1)
    scheduler.start()
    yield
    scheduler.shutdown()


app = FastAPI(title="NewsBot", version="0.3.0", lifespan=lifespan)

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# --- Health check (kept here as a trivial app-level endpoint) ---

@app.get("/health")
async def health():
    return {"status": "ok", "service": "newsbot"}


# --- Register routers ---

app.include_router(dashboard_router)
app.include_router(pipeline_router)
app.include_router(admin_api_router)
app.include_router(webhook_router)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        log_level=settings.log_level.lower(),
    )
