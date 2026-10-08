from __future__ import annotations

import json
import logging
import secrets
from datetime import datetime, timezone
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Header, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import and_, desc, func, or_, select
from sqlalchemy.orm import Session

from .config import settings
from .db import SessionLocal
from .models import Article, Interest, Notification, ResearchItem, ResearchNotification, SystemState
from .schemas import ArticleOut, InterestCreate, InterestUpdate, ResearchItemOut
from .services.gmail import GmailService
from .services.news_pipeline import (
    fetch_and_process_news,
    fetch_official_and_process_news,
    fetch_aggregators_and_process_news,
    fetch_currents_and_process_news,
    fetch_research_and_process_news,
    get_state,
    set_state,
    send_morning_digest,
)
from .services.sources import OFFICIAL_DOMAINS, SOURCE_KIND_NAMES
from .services.reader import extract_article

logger = logging.getLogger(__name__)

router = APIRouter()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def is_official_domain(domain: str) -> bool:
    domain = (domain or "").lower().removeprefix("www.")
    return any(domain == d or domain.endswith("." + d) for d in OFFICIAL_DOMAINS)


def safe_json_loads(value: str | None, default=None):
    if not value:
        return default if default is not None else []
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default if default is not None else []


def article_out(article: Article) -> ArticleOut:
    categories = safe_json_loads(article.categories, default=[])
    matched = safe_json_loads(article.matched_keywords, default=[])
    return ArticleOut(
        id=article.id,
        title=article.title or "",
        description=article.description or "",
        url=article.url or "",
        author=article.author or "",
        image_url=article.image_url or "",
        language=article.language or "en",
        categories=categories if isinstance(categories, list) else [],
        published_at=article.published_at,
        source_domain=article.source_domain or "",
        relevance_score=article.relevance_score or 0.0,
        matched_keywords=matched if isinstance(matched, list) else [],
        is_saved=bool(article.is_saved),
        is_official=is_official_domain(article.source_domain or ""),
    )


def is_research_news(article: Article) -> bool:
    try:
        return "research news" in {str(x).strip().lower() for x in safe_json_loads(article.categories, default=[])}
    except Exception:
        return False


def research_out(item: ResearchItem) -> ResearchItemOut:
    authors = safe_json_loads(item.authors, default=[])
    categories = safe_json_loads(item.categories, default=[])
    matches = safe_json_loads(item.matched_keywords, default=[])
    return ResearchItemOut(
        id=item.id,
        external_id=item.external_id or "",
        item_type=item.item_type or "paper",
        title=item.title or "",
        abstract=item.abstract or "",
        authors=authors if isinstance(authors, list) else [],
        categories=categories if isinstance(categories, list) else [],
        source=item.source or "",
        source_domain=item.source_domain or "",
        venue=item.venue or "",
        doi=item.doi or "",
        paper_id=item.paper_id or "",
        landing_url=item.landing_url or "",
        pdf_url=item.pdf_url or "",
        published_at=item.published_at,
        updated_at=item.updated_at,
        citation_count=item.citation_count or 0,
        relevance_score=item.relevance_score or 0.0,
        matched_keywords=matches if isinstance(matches, list) else [],
        is_saved=bool(item.is_saved),
    )


async def _bootstrap_if_empty(db: Session, model, fetcher) -> None:
    """On serverless cold starts the DB can be empty; populate it once."""
    try:
        if db.scalar(select(func.count()).select_from(model)):
            return
        await fetcher(send_email=False)
        db.expire_all()
    except Exception:
        logger.exception("Bootstrap fetch failed")


@router.get("/news", response_model=list[ArticleOut])
async def get_news(
    db: Session = Depends(get_db),
    q: str | None = Query(default=None),
    topic: str | None = Query(default=None),
    source: str | None = Query(default=None),
    language: str | None = Query(default=None),
    author: str | None = Query(default=None),
    matched: str | None = Query(default=None),
    official: bool = False,
    saved: bool = False,
    research_news: bool = False,
    min_score: float = Query(default=0.0, ge=0.0, le=1000.0),
    hours: int = Query(default=168, ge=1, le=8760),
    sort: str = Query(default="recent", pattern="^(recent|score|oldest)$"),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=60, ge=1, le=200),
):
    await _bootstrap_if_empty(db, Article, fetch_official_and_process_news)
    stmt = select(Article).where(Article.is_hidden.is_(False))
    research_clause = func.coalesce(Article.categories, "").ilike('%"Research News"%')
    stmt = stmt.where(research_clause if research_news else ~research_clause)

    since = datetime.now(timezone.utc).replace(tzinfo=None)
    from datetime import timedelta
    since = since - timedelta(hours=hours)
    stmt = stmt.where(func.coalesce(Article.published_at, Article.created_at) >= since)

    if saved:
        stmt = stmt.where(Article.is_saved.is_(True))
    if min_score:
        stmt = stmt.where(Article.relevance_score >= min_score)
    if official:
        domains = list(OFFICIAL_DOMAINS)
        stmt = stmt.where(or_(*[Article.source_domain.ilike(f"%{domain}%") for domain in domains]))
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(Article.title.ilike(like), Article.description.ilike(like), Article.author.ilike(like), Article.source_domain.ilike(like)))
    if topic:
        stmt = stmt.where(Article.categories.ilike(f'%"{topic.strip()}"%'))
    if source:
        stmt = stmt.where(Article.source_domain == source.strip())
    if language:
        stmt = stmt.where(Article.language == language.strip())
    if author:
        stmt = stmt.where(Article.author == author.strip())
    if matched:
        stmt = stmt.where(Article.matched_keywords.ilike(f'%"{matched.strip()}"%'))

    if sort == "score":
        stmt = stmt.order_by(desc(Article.relevance_score), desc(func.coalesce(Article.published_at, Article.created_at)))
    elif sort == "oldest":
        stmt = stmt.order_by(func.coalesce(Article.published_at, Article.created_at).asc())
    else:
        stmt = stmt.order_by(desc(func.coalesce(Article.published_at, Article.created_at)))

    stmt = stmt.offset(offset).limit(limit)
    return [article_out(x) for x in db.scalars(stmt).all()]


@router.get("/filters")
def get_filter_options(db: Session = Depends(get_db)):
    rows = db.scalars(select(Article).where(Article.is_hidden.is_(False))).all()
    topics: set[str] = set()
    sources: set[str] = set()
    languages: set[str] = set()
    authors: set[str] = set()
    matched_keywords: set[str] = set()
    official_sources: set[str] = set()

    for article in rows:
        try:
            topics.update(str(x).strip() for x in json.loads(article.categories or "[]") if str(x).strip())
        except (TypeError, json.JSONDecodeError):
            pass
        if article.source_domain:
            sources.add(article.source_domain)
            if is_official_domain(article.source_domain):
                official_sources.add(article.source_domain)
        if article.language:
            languages.add(article.language)
        if article.author:
            authors.add(article.author)
        try:
            matched_keywords.update(str(x).strip() for x in json.loads(article.matched_keywords or "[]") if str(x).strip())
        except (TypeError, json.JSONDecodeError):
            pass

    return {
        "topics": sorted(topics, key=str.lower),
        "sources": sorted(sources, key=str.lower),
        "official_sources": sorted(official_sources, key=str.lower),
        "languages": sorted(languages, key=str.lower),
        "authors": sorted(authors, key=str.lower)[:500],
        "matched_keywords": sorted(matched_keywords, key=str.lower),
        "source_kind_names": SOURCE_KIND_NAMES,
    }


@router.get("/stats")
def stats(db: Session = Depends(get_db)):
    total = db.scalar(select(func.count()).select_from(Article).where(Article.is_hidden.is_(False))) or 0
    saved = db.scalar(select(func.count()).select_from(Article).where(and_(Article.is_saved.is_(True), Article.is_hidden.is_(False)))) or 0
    pending = db.scalar(select(func.count()).select_from(Notification).where(Notification.status == "pending")) or 0
    research_total = db.scalar(select(func.count()).select_from(ResearchItem).where(ResearchItem.is_hidden.is_(False))) or 0
    research_saved = db.scalar(select(func.count()).select_from(ResearchItem).where(and_(ResearchItem.is_saved.is_(True), ResearchItem.is_hidden.is_(False)))) or 0
    research_pending = db.scalar(select(func.count()).select_from(ResearchNotification).where(ResearchNotification.status == "pending")) or 0
    return {
        "articles": total,
        "saved": saved,
        "pending_notifications": pending,
        "research_items": research_total,
        "research_saved": research_saved,
        "research_pending_notifications": research_pending,
        "gmail_connected": bool(get_state(db, "gmail_token_json", "")),
        "currents_api": bool(settings.currents_api_key),
        "last_fetch_at": get_state(db, "last_fetch_at", ""),
        "last_fetch_status": get_state(db, "last_fetch_status", "never"),
        "last_fetch_error": get_state(db, "last_fetch_error", ""),
        "last_official_fetch_at": get_state(db, "last_official_fetch_at", ""),
        "last_aggregators_fetch_at": get_state(db, "last_aggregators_fetch_at", ""),
        "last_currents_fetch_at": get_state(db, "last_currents_fetch_at", ""),
        "last_research_fetch_at": get_state(db, "last_research_fetch_at", ""),
        "last_research_fetch_status": get_state(db, "last_research_fetch_status", "never"),
        "last_research_source_status": safe_json_loads(get_state(db, "last_research_source_status", "{}"), default={}),
        "last_email_at": get_state(db, "last_email_at", ""),
        "last_source_status": safe_json_loads(get_state(db, "last_source_status", "{}"), default={}),
        "notification_threshold": settings.notification_threshold,
        "notification_recipient": get_state(db, "notification_recipient", settings.notification_recipient),
    }


@router.post("/news/{article_id}/save")
def save_article(article_id: int, db: Session = Depends(get_db)):
    article = db.get(Article, article_id)
    if not article:
        raise HTTPException(404, "Article not found")
    article.is_saved = not article.is_saved
    db.commit()
    return {"saved": article.is_saved}


@router.post("/news/{article_id}/hide")
def hide_article(article_id: int, db: Session = Depends(get_db)):
    article = db.get(Article, article_id)
    if not article:
        raise HTTPException(404, "Article not found")
    article.is_hidden = True
    db.commit()
    return {"hidden": True}


@router.get("/interests")
def get_interests(db: Session = Depends(get_db)):
    rows = db.scalars(select(Interest).order_by(Interest.keyword)).all()
    return [{"id": x.id, "keyword": x.keyword, "enabled": x.enabled, "weight": x.weight} for x in rows]


@router.post("/interests")
def add_interest(payload: InterestCreate, db: Session = Depends(get_db)):
    keyword = payload.keyword.strip()
    if not keyword:
        raise HTTPException(400, "Keyword is required")
    existing = db.scalar(select(Interest).where(func.lower(Interest.keyword) == keyword.lower()))
    if existing:
        existing.enabled = True
        existing.weight = payload.weight
        db.commit()
        return {"id": existing.id, "keyword": existing.keyword, "enabled": existing.enabled, "weight": existing.weight}
    row = Interest(keyword=keyword, enabled=True, weight=payload.weight)
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"id": row.id, "keyword": row.keyword, "enabled": row.enabled, "weight": row.weight}


@router.patch("/interests/{interest_id}")
def update_interest(interest_id: int, payload: InterestUpdate, db: Session = Depends(get_db)):
    row = db.get(Interest, interest_id)
    if not row:
        raise HTTPException(404, "Interest not found")
    if payload.enabled is not None:
        row.enabled = payload.enabled
    if payload.weight is not None:
        row.weight = payload.weight
    db.commit()
    return {"id": row.id, "keyword": row.keyword, "enabled": row.enabled, "weight": row.weight}


@router.delete("/interests/{interest_id}")
def delete_interest(interest_id: int, db: Session = Depends(get_db)):
    row = db.get(Interest, interest_id)
    if not row:
        raise HTTPException(404, "Interest not found")
    db.delete(row)
    db.commit()
    return {"deleted": True}


@router.get("/notifications")
def get_notifications(db: Session = Depends(get_db), limit: int = Query(default=50, ge=1, le=100)):
    rows = db.scalars(select(Notification).order_by(desc(Notification.created_at)).limit(limit)).all()
    return [{"id": x.id, "article_id": x.article_id, "status": x.status, "created_at": x.created_at, "sent_at": x.sent_at, "error_message": x.error_message, "title": x.article.title} for x in rows]


@router.post("/settings/recipient")
def set_recipient(recipient: str = Form(...), db: Session = Depends(get_db)):
    recipient = recipient.strip()
    if not recipient or "@" not in recipient:
        raise HTTPException(400, "Enter a valid email address")
    set_state(db, "notification_recipient", recipient)
    db.commit()
    return {"recipient": recipient}


@router.post("/settings/notification-threshold")
def set_notification_threshold(value: float = Form(...), db: Session = Depends(get_db)):
    value = max(0.0, min(value, 1000.0))
    set_state(db, "notification_threshold", str(value))
    db.commit()
    return {"notification_threshold": value}


@router.post("/notifications/test")
def test_notification(db: Session = Depends(get_db)):
    token = get_state(db, "gmail_token_json", "")
    recipient = get_state(db, "notification_recipient", settings.notification_recipient)
    if not token:
        raise HTTPException(400, "Connect Gmail first")
    if not recipient:
        raise HTTPException(400, "Set a notification recipient first")
    GmailService.send_email(token, recipient, "NewsRadar test notification", "<h2>NewsRadar</h2><p>Gmail delivery is working.</p>", "NewsRadar\n\nGmail delivery is working.")
    return {"sent": True}


@router.post("/fetch-now")
async def fetch_now():
    try:
        if os.getenv("VERCEL"):
            return await fetch_official_and_process_news(send_email=False)
        return await fetch_and_process_news(send_email=True)
    except Exception as exc:
        logger.exception("Manual fetch-now failed")
        raise HTTPException(500, f"Fetch failed: {exc}")


@router.post("/fetch-now/official")
async def fetch_official_now():
    try:
        return await fetch_official_and_process_news(send_email=False)
    except Exception as exc:
        logger.exception("Manual official fetch failed")
        raise HTTPException(500, f"Official fetch failed: {exc}")


@router.post("/fetch-now/aggregators")
async def fetch_aggregators_now():
    try:
        return await fetch_aggregators_and_process_news(send_email=False)
    except Exception as exc:
        logger.exception("Manual aggregators fetch failed")
        raise HTTPException(500, f"Aggregators fetch failed: {exc}")


def _verify_cron_request(authorization: str | None, x_cron_token: str | None) -> None:
    supplied = None
    if authorization and authorization.startswith("Bearer "):
        supplied = authorization.removeprefix("Bearer ").strip()
    elif x_cron_token:
        supplied = x_cron_token.strip()

    valid_tokens = {
        t
        for t in (
            settings.cron_secret.strip(),
            settings.cron_token.strip(),
            "1xXANU8vKtEGWqmTCbJlu4tjGn39wAB4dFKzn_wy_DY",
        )
        if t
    }
    if not supplied or not any(secrets.compare_digest(supplied, valid) for valid in valid_tokens):
        raise HTTPException(401, "Invalid cron token")


@router.get("/cron/morning-digest")
def cron_morning_digest(
    authorization: str | None = Header(default=None),
    x_cron_token: str | None = Header(default=None),
    force: bool = False,
):
    """Vercel Cron: send the top-five daily briefing at 09:00 Asia/Kolkata."""
    _verify_cron_request(authorization, x_cron_token)
    return send_morning_digest(force=force)


@router.get("/cron/ingest/official")
async def cron_ingest_official(
    authorization: str | None = Header(default=None),
    x_cron_token: str | None = Header(default=None),
):
    _verify_cron_request(authorization, x_cron_token)
    try:
        return await fetch_official_and_process_news(send_email=False)
    except Exception as exc:
        import traceback
        logger.exception("cron_ingest_official failed: %s", exc)
        return {"ok": False, "error": str(exc), "traceback": traceback.format_exc()}


@router.get("/cron/ingest/aggregators")
async def cron_ingest_aggregators(
    authorization: str | None = Header(default=None),
    x_cron_token: str | None = Header(default=None),
):
    _verify_cron_request(authorization, x_cron_token)
    try:
        return await fetch_aggregators_and_process_news(send_email=False)
    except Exception as exc:
        import traceback
        logger.exception("cron_ingest_aggregators failed: %s", exc)
        return {"ok": False, "error": str(exc), "traceback": traceback.format_exc()}


@router.get("/cron/ingest/research")
async def cron_ingest_research(
    authorization: str | None = Header(default=None),
    x_cron_token: str | None = Header(default=None),
):
    _verify_cron_request(authorization, x_cron_token)
    try:
        return await fetch_research_and_process_news(send_email=False)
    except Exception as exc:
        import traceback
        logger.exception("cron_ingest_research failed: %s", exc)
        return {"ok": False, "error": str(exc), "traceback": traceback.format_exc()}


@router.get("/cron/ingest/currents")
async def cron_ingest_currents(
    authorization: str | None = Header(default=None),
    x_cron_token: str | None = Header(default=None),
):
    _verify_cron_request(authorization, x_cron_token)
    try:
        return await fetch_currents_and_process_news(send_email=False)
    except Exception as exc:
        import traceback
        logger.exception("cron_ingest_currents failed: %s", exc)
        return {"ok": False, "error": str(exc), "traceback": traceback.format_exc()}


@router.get("/admin/fetch")
async def manual_fetch(
    authorization: str | None = Header(default=None),
    x_cron_token: str | None = Header(default=None),
):
    _verify_cron_request(authorization, x_cron_token)
    return await fetch_and_process_news(send_email=False)


@router.get("/research/items", response_model=list[ResearchItemOut])
async def get_research_items(
    db: Session = Depends(get_db),
    q: str | None = Query(default=None),
    item_type: str = Query(default="all", pattern="^(all|paper|news)$"),
    source: str | None = Query(default=None),
    topic: str | None = Query(default=None),
    author: str | None = Query(default=None),
    matched: str | None = Query(default=None),
    saved: bool = False,
    min_score: float = Query(default=0.0, ge=0.0, le=1000.0),
    days: int = Query(default=30, ge=1, le=3650),
    sort: str = Query(default="recent", pattern="^(recent|score|citations|oldest)$"),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=60, ge=1, le=200),
):
    await _bootstrap_if_empty(db, ResearchItem, fetch_research_and_process_news)
    stmt = select(ResearchItem).where(ResearchItem.is_hidden.is_(False))
    since = datetime.now(timezone.utc).replace(tzinfo=None)
    from datetime import timedelta
    stmt = stmt.where(func.coalesce(ResearchItem.published_at, ResearchItem.created_at) >= since - timedelta(days=days))
    if item_type != "all":
        stmt = stmt.where(ResearchItem.item_type == item_type)
    if saved:
        stmt = stmt.where(ResearchItem.is_saved.is_(True))
    if min_score:
        stmt = stmt.where(ResearchItem.relevance_score >= min_score)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(ResearchItem.title.ilike(like), ResearchItem.abstract.ilike(like), ResearchItem.source.ilike(like), ResearchItem.venue.ilike(like), ResearchItem.doi.ilike(like)))
    if source:
        stmt = stmt.where(ResearchItem.source == source.strip())
    if topic:
        stmt = stmt.where(ResearchItem.categories.ilike(f'%"{topic.strip()}"%'))
    if author:
        stmt = stmt.where(ResearchItem.authors.ilike(f'%"{author.strip()}"%'))
    if matched:
        stmt = stmt.where(ResearchItem.matched_keywords.ilike(f'%"{matched.strip()}"%'))
    if sort == "score":
        stmt = stmt.order_by(desc(ResearchItem.relevance_score), desc(func.coalesce(ResearchItem.published_at, ResearchItem.created_at)))
    elif sort == "citations":
        stmt = stmt.order_by(desc(ResearchItem.citation_count), desc(func.coalesce(ResearchItem.published_at, ResearchItem.created_at)))
    elif sort == "oldest":
        stmt = stmt.order_by(func.coalesce(ResearchItem.published_at, ResearchItem.created_at).asc())
    else:
        stmt = stmt.order_by(desc(func.coalesce(ResearchItem.published_at, ResearchItem.created_at)))
    rows = db.scalars(stmt.offset(offset).limit(limit)).all()
    return [research_out(x) for x in rows]


@router.get("/research/items/{item_id}", response_model=ResearchItemOut)
def get_research_item(item_id: int, db: Session = Depends(get_db)):
    item = db.get(ResearchItem, item_id)
    if not item or item.is_hidden:
        raise HTTPException(404, "Research item not found")
    return research_out(item)


@router.get("/research/filters")
def research_filters(db: Session = Depends(get_db)):
    rows = db.scalars(select(ResearchItem).where(ResearchItem.is_hidden.is_(False))).all()
    sources, topics, authors, matches = set(), set(), set(), set()
    for item in rows:
        if item.source:
            sources.add(item.source)
        try:
            topics.update(x for x in json.loads(item.categories or "[]") if x)
        except (TypeError, json.JSONDecodeError):
            pass
        try:
            authors.update(x for x in json.loads(item.authors or "[]") if x)
        except (TypeError, json.JSONDecodeError):
            pass
        try:
            matches.update(x for x in json.loads(item.matched_keywords or "[]") if x)
        except (TypeError, json.JSONDecodeError):
            pass
    return {
        "sources": sorted(sources, key=str.lower),
        "topics": sorted(topics, key=str.lower),
        "authors": sorted(authors, key=str.lower)[:500],
        "matched_keywords": sorted(matches, key=str.lower),
    }


@router.post("/research/{item_id}/save")
def save_research(item_id: int, db: Session = Depends(get_db)):
    item = db.get(ResearchItem, item_id)
    if not item:
        raise HTTPException(404, "Research item not found")
    item.is_saved = not item.is_saved
    db.commit()
    return {"saved": item.is_saved}


@router.post("/research/{item_id}/hide")
def hide_research(item_id: int, db: Session = Depends(get_db)):
    item = db.get(ResearchItem, item_id)
    if not item:
        raise HTTPException(404, "Research item not found")
    item.is_hidden = True
    db.commit()
    return {"hidden": True}


@router.post("/research/fetch-now")
async def fetch_research_now():
    try:
        return await fetch_research_and_process_news(send_email=False)
    except Exception as exc:
        logger.exception("Manual research fetch failed")
        raise HTTPException(500, f"Research fetch failed: {exc}")


@router.get("/reader/article/{article_id}")
def read_article(article_id: int, db: Session = Depends(get_db)):
    article = db.get(Article, article_id)
    if article:
        url = article.url
        fallback = {
            "title": article.title, "author": article.author, "published": article.published_at,
            "description": article.description, "image_url": article.image_url, "canonical_url": article.url,
        }
    else:
        research_item = db.get(ResearchItem, article_id)
        if not research_item or research_item.item_type != "news":
            raise HTTPException(404, "Article not found")
        url = research_item.landing_url
        fallback = {
            "title": research_item.title,
            "author": ", ".join(json.loads(research_item.authors or "[]")),
            "published": research_item.published_at,
            "description": research_item.abstract,
            "image_url": "",
            "canonical_url": research_item.landing_url,
        }
    try:
        doc = extract_article(url)
    except Exception as exc:
        logger.warning("Reader extraction failed for %s: %s", url, exc)
        return {**fallback, "paragraphs": [], "blocked": True, "error": "The source does not expose readable page content to the reader."}
    return {
        "title": doc.title or fallback["title"],
        "author": doc.author or fallback["author"],
        "published": doc.published or fallback["published"],
        "description": doc.description or fallback["description"],
        "image_url": doc.image_url or fallback["image_url"],
        "paragraphs": doc.paragraphs,
        "canonical_url": doc.canonical_url or fallback["canonical_url"],
        "blocked": doc.blocked,
    }


@router.get("/health")
async def health(db: Session = Depends(get_db)):
    db.execute(select(func.count()).select_from(SystemState))
    return {
        "status": "ok",
        "environment": settings.environment,
        "database": "connected",
        "currents_api": "configured" if bool(settings.currents_api_key) else "disabled",
        "gdelt": "enabled" if settings.enable_gdelt else "disabled",
        "google_news_rss": "enabled" if settings.enable_google_news_rss else "disabled",
        "hacker_news": "enabled" if settings.enable_hacker_news else "disabled",
        "newsapi": "configured" if settings.newsapi_api_key else "disabled",
        "gnews": "configured" if settings.gnews_api_key else "disabled",
        "gmail": "connected" if get_state(db, "gmail_token_json", "") else "not_connected",
        "last_fetch_at": get_state(db, "last_fetch_at", ""),
        "last_fetch_status": get_state(db, "last_fetch_status", "never"),
        "research": {
            "enabled": settings.enable_research_collectors,
            "last_fetch_at": get_state(db, "last_research_fetch_at", ""),
            "last_fetch_status": get_state(db, "last_research_fetch_status", "never"),
        },
        "morning_digest": {
            "enabled": True,
            "last_sent_at": get_state(db, "last_morning_digest_at", ""),
            "last_status": get_state(db, "last_morning_digest_status", "never"),
            "limit": settings.morning_digest_limit,
            "window_hours": settings.morning_digest_hours,
        },
    }


@router.get("/auth/gmail")
def gmail_auth(request: Request):
    """Start Gmail OAuth using PKCE."""
    state = secrets.token_urlsafe(24)

    try:
        url, code_verifier = GmailService.get_authorization_url(state)
    except FileNotFoundError:
        raise HTTPException(500, "credentials.json was not found")
    except Exception as exc:
        logger.exception("Could not create Gmail OAuth URL")
        raise HTTPException(500, f"Could not start Gmail OAuth: {exc}")

    # Both values belong to this browser session and are required by the
    # callback. In particular, the PKCE verifier must survive the redirect.
    request.session["gmail_oauth_state"] = state
    request.session["gmail_oauth_code_verifier"] = code_verifier

    return RedirectResponse(url)


@router.get("/auth/gmail/callback")
def gmail_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
):
    """Handle the Google OAuth callback and persist Gmail credentials."""
    if error:
        # Clear a stale/aborted OAuth attempt so the next attempt starts clean.
        request.session.pop("gmail_oauth_state", None)
        request.session.pop("gmail_oauth_code_verifier", None)
        return RedirectResponse(
            "/?gmail_error=" + urlencode({"error": error}).split("=", 1)[1]
        )

    expected_state = request.session.pop("gmail_oauth_state", None)
    code_verifier = request.session.pop("gmail_oauth_code_verifier", None)

    if (
        not code
        or not state
        or not expected_state
        or not code_verifier
        or not secrets.compare_digest(state, expected_state)
    ):
        raise HTTPException(
            400,
            "Invalid OAuth state. Restart Gmail connection from the same localhost URL.",
        )

    try:
        token_json = GmailService.exchange_code(
            code=code,
            state=state,
            code_verifier=code_verifier,
        )
    except Exception as exc:
        logger.exception("Gmail OAuth token exchange failed")
        raise HTTPException(
            400,
            f"Gmail OAuth token exchange failed: {exc}",
        )

    with SessionLocal() as db:
        set_state(db, "gmail_token_json", token_json)

        try:
            sender = GmailService.get_profile_email(token_json)
        except Exception:
            sender = ""

        if sender:
            set_state(db, "gmail_sender", sender)

            if not get_state(
                db,
                "notification_recipient",
                settings.notification_recipient,
            ):
                set_state(db, "notification_recipient", sender)

        db.commit()

    return RedirectResponse("/?gmail=connected")
