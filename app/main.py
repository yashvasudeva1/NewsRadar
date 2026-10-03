import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from .api import router
from .config import settings
from .db import init_db
from .services.news_pipeline import (
    seed_interests,
    fetch_official_and_process_news,
    fetch_aggregators_and_process_news,
    fetch_currents_and_process_news,
    fetch_research_and_process_news,
)


logging.basicConfig(level=logging.INFO)

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BASE_DIR.parent


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application startup/shutdown lifecycle.

    On Vercel:
        No persistent scheduler is started.

    Locally:
        APScheduler is started so the application can continuously
        collect news and research.
    """

    # ---------------------------------------------------------
    # Database initialization
    # ---------------------------------------------------------

    (PROJECT_DIR / "data").mkdir(parents=True, exist_ok=True)

    init_db()
    seed_interests()

    # ---------------------------------------------------------
    # Detect Vercel
    # ---------------------------------------------------------

    is_vercel = bool(os.getenv("VERCEL"))

    # ---------------------------------------------------------
    # Local scheduler only
    # ---------------------------------------------------------

    if settings.enable_scheduler and not is_vercel:

        # IMPORTANT:
        # APScheduler is imported only here.
        # Vercel therefore doesn't need APScheduler merely to
        # import and initialize the FastAPI application.

        from apscheduler.schedulers.asyncio import AsyncIOScheduler

        scheduler = AsyncIOScheduler()

        # Official sources
        scheduler.add_job(
            fetch_official_and_process_news,
            "interval",
            minutes=max(5, settings.official_poll_minutes),
            id="official-news",
            replace_existing=True,
            coalesce=True,
            max_instances=1,
            misfire_grace_time=120,
            next_run_time=datetime.now(timezone.utc)
            + timedelta(seconds=3),
        )

        # Aggregators
        scheduler.add_job(
            fetch_aggregators_and_process_news,
            "interval",
            minutes=max(10, settings.aggregator_poll_minutes),
            id="aggregator-news",
            replace_existing=True,
            coalesce=True,
            max_instances=1,
            misfire_grace_time=120,
            next_run_time=datetime.now(timezone.utc)
            + timedelta(seconds=7),
        )

        # Currents
        if settings.currents_api_key:

            scheduler.add_job(
                fetch_currents_and_process_news,
                "interval",
                minutes=max(15, settings.currents_poll_minutes),
                id="currents-news",
                replace_existing=True,
                coalesce=True,
                max_instances=1,
                misfire_grace_time=120,
                next_run_time=datetime.now(timezone.utc)
                + timedelta(seconds=10),
            )

        # Research
        if settings.enable_research_collectors:

            scheduler.add_job(
                fetch_research_and_process_news,
                "interval",
                minutes=max(15, settings.research_poll_minutes),
                id="research-news",
                replace_existing=True,
                coalesce=True,
                max_instances=1,
                misfire_grace_time=120,
                next_run_time=datetime.now(timezone.utc)
                + timedelta(seconds=14),
            )

        scheduler.start()

        logger.info(
            "Local scheduler started: "
            "official=%sm, aggregators=%sm, currents=%sm, research=%sm",
            settings.official_poll_minutes,
            settings.aggregator_poll_minutes,
            settings.currents_poll_minutes,
            settings.research_poll_minutes,
        )

    else:

        if is_vercel:
            logger.info(
                "Running on Vercel. Persistent APScheduler disabled."
            )
        else:
            logger.info(
                "Scheduler disabled by configuration."
            )

    # ---------------------------------------------------------
    # Application is ready
    # ---------------------------------------------------------

    yield

    # ---------------------------------------------------------
    # Shutdown
    # ---------------------------------------------------------

    if settings.enable_scheduler and not is_vercel:
        try:
            scheduler.shutdown(wait=False)
            logger.info("Local scheduler stopped.")
        except Exception:
            pass


# ---------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------

app = FastAPI(
    title=settings.app_name,
    version="5.0.1",
    lifespan=lifespan,
)


# ---------------------------------------------------------
# Trusted hosts
# ---------------------------------------------------------

allowed_hosts = [
    h.strip()
    for h in settings.allowed_hosts.split(",")
    if h.strip()
]

if allowed_hosts and allowed_hosts != ["*"]:

    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=allowed_hosts,
    )


# ---------------------------------------------------------
# Session middleware
# ---------------------------------------------------------

app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key,
    same_site="lax",
    https_only=settings.environment.lower() == "production",
)


# ---------------------------------------------------------
# Static files
# ---------------------------------------------------------

app.mount(
    "/static",
    StaticFiles(directory=str(BASE_DIR / "static")),
    name="static",
)


# ---------------------------------------------------------
# API
# ---------------------------------------------------------

app.include_router(
    router,
    prefix="/api",
)


# ---------------------------------------------------------
# Templates
# ---------------------------------------------------------

templates = Jinja2Templates(
    directory=str(BASE_DIR / "templates"),
)


# ---------------------------------------------------------
# Dashboard
# ---------------------------------------------------------

@app.get(
    "/",
    response_class=HTMLResponse,
)
def dashboard(request: Request):

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "app_name": settings.app_name,
        },
    )


# ---------------------------------------------------------
# Settings
# ---------------------------------------------------------

@app.get("/settings")
def settings_page():

    return RedirectResponse("/")


# ---------------------------------------------------------
# News reader
# ---------------------------------------------------------

@app.get(
    "/read/news/{article_id}",
    response_class=HTMLResponse,
)
def article_reader(
    request: Request,
    article_id: int,
):

    return templates.TemplateResponse(
        request=request,
        name="reader.html",
        context={
            "app_name": settings.app_name,
            "article_id": article_id,
        },
    )


# ---------------------------------------------------------
# Research news reader
# ---------------------------------------------------------

@app.get(
    "/read/news-research/{item_id}",
    response_class=HTMLResponse,
)
def research_news_reader(
    request: Request,
    item_id: int,
):

    return templates.TemplateResponse(
        request=request,
        name="reader.html",
        context={
            "app_name": settings.app_name,
            "article_id": item_id,
        },
    )


# ---------------------------------------------------------
# Research paper reader
# ---------------------------------------------------------

@app.get(
    "/read/paper/{item_id}",
    response_class=HTMLResponse,
)
def paper_reader(
    request: Request,
    item_id: int,
):

    return templates.TemplateResponse(
        request=request,
        name="paper_reader.html",
        context={
            "app_name": settings.app_name,
            "item_id": item_id,
        },
    )
