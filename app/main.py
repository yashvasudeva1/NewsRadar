import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from .api import router
from .config import settings
from .db import init_db
from .services.news_pipeline import seed_interests


# =========================================================
# Logging
# =========================================================

logging.basicConfig(
    level=logging.INFO,
)

logger = logging.getLogger(__name__)


# =========================================================
# Paths
# =========================================================

BASE_DIR = Path(__file__).resolve().parent


# =========================================================
# Application lifespan
# =========================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application startup/shutdown lifecycle.

    Vercel runs this application as a serverless function,
    so persistent background tasks are disabled on Vercel.
    In local development and self-hosted environments, a lightweight
    asyncio task handles periodic background ingestion.
    """

    # -----------------------------------------------------
    # Database initialization
    # -----------------------------------------------------

    try:
        if not os.getenv("VERCEL"):
            (BASE_DIR.parent / "data").mkdir(parents=True, exist_ok=True)
        init_db()
        seed_interests()

        logger.info(
            "NewsRadar startup completed successfully."
        )

    except Exception:
        logger.exception(
            "NewsRadar startup failed."
        )
        raise

    background_task = None
    if not os.getenv("VERCEL") and settings.enable_scheduler:
        async def background_collector():
            logger.info("Local background collector started.")
            try:
                if settings.initial_fetch_on_startup:
                    await asyncio.sleep(2)
                    try:
                        logger.info("Running initial background news ingestion...")
                        from .services.news_pipeline import (
                            fetch_official_and_process_news,
                            fetch_aggregators_and_process_news,
                        )
                        await fetch_official_and_process_news(send_email=False)
                        await fetch_aggregators_and_process_news(send_email=False)
                    except Exception as exc:
                        logger.warning("Initial background ingestion failed: %s", exc)

                while True:
                    await asyncio.sleep(max(60, settings.official_poll_minutes * 60))
                    try:
                        from .services.news_pipeline import fetch_official_and_process_news
                        await fetch_official_and_process_news(send_email=False)
                    except asyncio.CancelledError:
                        break
                    except Exception as exc:
                        logger.warning("Periodic background ingestion failed: %s", exc)
            except asyncio.CancelledError:
                pass

        background_task = asyncio.create_task(background_collector())

    yield

    # -----------------------------------------------------
    # Shutdown
    # -----------------------------------------------------

    if background_task:
        background_task.cancel()
        try:
            await background_task
        except asyncio.CancelledError:
            pass

    logger.info(
        "NewsRadar application shutting down."
    )


# =========================================================
# FastAPI application
# =========================================================

app = FastAPI(
    title=settings.app_name,
    version="5.0.1",
    description=(
        "Personal technology news and research monitor "
        "with Gmail notifications."
    ),
    lifespan=lifespan,
    redirect_slashes=False,
)


@app.middleware("http")
async def normalize_path_middleware(request: Request, call_next):
    path = request.scope.get("path", "")
    if path == "/main.py":
        request.scope["path"] = "/"
    elif path.startswith("/main.py/"):
        request.scope["path"] = path[len("/main.py"):]
    return await call_next(request)


# =========================================================
# Trusted host middleware
# =========================================================

allowed_hosts = [
    host.strip()
    for host in settings.allowed_hosts.split(",")
    if host.strip()
]

if allowed_hosts and allowed_hosts != ["*"]:
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=allowed_hosts,
    )


# =========================================================
# Session middleware
# =========================================================

app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key,
    same_site="lax",
    https_only=(
        settings.environment.lower() == "production"
    ),
)


# =========================================================
# Static files
# =========================================================

app.mount(
    "/static",
    StaticFiles(
        directory=str(BASE_DIR / "static"),
    ),
    name="static",
)


# =========================================================
# API routes
# =========================================================

app.include_router(
    router,
    prefix="/api",
)


# =========================================================
# Templates
# =========================================================

templates = Jinja2Templates(
    directory=str(BASE_DIR / "templates"),
)


# =========================================================
# Dashboard
# =========================================================

@app.get(
    "/",
    response_class=HTMLResponse,
)
def dashboard(request: Request):
    """
    Main NewsRadar dashboard.
    """

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "app_name": settings.app_name,
        },
    )


# =========================================================
# Settings
# =========================================================

@app.get(
    "/settings",
)
def settings_page():
    """
    Settings are handled from the dashboard UI.
    """

    return RedirectResponse(
        url="/",
        status_code=307,
    )


@app.get(
    "/favicon.ico",
    include_in_schema=False,
)
def favicon():
    logo_file = BASE_DIR / "static" / "logo.svg"
    if logo_file.exists():
        return Response(
            content=logo_file.read_bytes(),
            media_type="image/svg+xml",
            headers={"Cache-Control": "public, max-age=86400"},
        )
    return Response(status_code=204)


@app.get(
    "/favicon.svg",
    include_in_schema=False,
)
def favicon_svg():
    logo_file = BASE_DIR / "static" / "logo.svg"
    if logo_file.exists():
        return Response(
            content=logo_file.read_bytes(),
            media_type="image/svg+xml",
            headers={"Cache-Control": "public, max-age=86400"},
        )
    return Response(status_code=404)


# =========================================================
# News reader
# =========================================================

@app.get(
    "/read/news/{article_id}",
    response_class=HTMLResponse,
)
def article_reader(
    request: Request,
    article_id: int,
):
    """
    Internal reader for technology news articles.
    """

    return templates.TemplateResponse(
        request=request,
        name="reader.html",
        context={
            "app_name": settings.app_name,
            "article_id": article_id,
        },
    )


# =========================================================
# Research news reader
# =========================================================

@app.get(
    "/read/news-research/{item_id}",
    response_class=HTMLResponse,
)
def research_news_reader(
    request: Request,
    item_id: int,
):
    """
    Internal reader for research-related news.
    """

    return templates.TemplateResponse(
        request=request,
        name="reader.html",
        context={
            "app_name": settings.app_name,
            "article_id": item_id,
        },
    )


# =========================================================
# Research paper reader
# =========================================================

@app.get(
    "/read/paper/{item_id}",
    response_class=HTMLResponse,
)
def paper_reader(
    request: Request,
    item_id: int,
):
    """
    Research paper reader with abstract/metadata/PDF support.
    """

    return templates.TemplateResponse(
        request=request,
        name="paper_reader.html",
        context={
            "app_name": settings.app_name,
            "item_id": item_id,
        },
    )
