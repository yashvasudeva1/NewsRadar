import logging
from contextlib import asynccontextmanager
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
    so there is intentionally NO persistent background scheduler.

    Scheduled tasks such as:
        - news ingestion
        - research ingestion
        - morning Gmail digest

    are handled through HTTP cron endpoints.
    """

    # -----------------------------------------------------
    # Database initialization
    # -----------------------------------------------------

    try:
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

    yield

    # -----------------------------------------------------
    # Shutdown
    # -----------------------------------------------------

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
)


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
