from __future__ import annotations

import hashlib
import html
import json
import logging
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
import os

from sqlalchemy import select, or_, func

from ..config import settings
from ..db import session_scope
from ..models import Article, Interest, Notification, ResearchItem, ResearchNotification, SystemState
from .currents import CurrentsClient
from .filtering import categories_json, classify_topics, is_technology, score_article
from .gmail import GmailService
from .multi_source import fetch_community, fetch_gdelt, fetch_google_news_rss, fetch_official_sources, fetch_newsapi, fetch_gnews
from .research import collect_research, ingest_research
from .sources import OFFICIAL_DOMAINS

logger = logging.getLogger(__name__)


def set_state(db, key: str, value: str) -> None:
    row = db.scalar(select(SystemState).where(SystemState.key == key))
    if row is None:
        db.add(SystemState(key=key, value=value))
    else:
        row.value = value


def get_state(db, key: str, default: str = "") -> str:
    row = db.scalar(select(SystemState).where(SystemState.key == key))
    return row.value if row else default


def stable_hash(title: str, url: str = "") -> str:
    raw = f"{title.strip().lower()}|{url.strip().lower()}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def title_hash(title: str) -> str:
    raw = " ".join(title.lower().split()).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def seed_interests() -> None:
    defaults = [x.strip() for x in settings.default_keywords.split(",") if x.strip()]
    with session_scope() as db:
        existing = {x.lower() for x in db.scalars(select(Interest.keyword)).all()}
        for keyword in defaults:
            if keyword.lower() not in existing:
                db.add(Interest(keyword=keyword, enabled=True, weight=1.0))


def gmail_token(db) -> str:
    return get_state(db, "gmail_token_json", "")


def recipient(db) -> str:
    return get_state(db, "notification_recipient", settings.notification_recipient)


def escape(value: str) -> str:
    return html.escape(value or "")


def build_digest(items: list[Article]) -> tuple[str, str, str]:
    subject = f"NewsRadar — {len(items)} new tech {('story' if len(items) == 1 else 'stories')}"
    html_items = []
    text_items = []
    for i, article in enumerate(items, start=1):
        matched = ", ".join(json.loads(article.matched_keywords or "[]")) or "technology"
        published = article.published_at.astimezone(timezone.utc).strftime("%d %b %Y, %H:%M UTC") if article.published_at else ""
        html_items.append(
            f"<li style='margin-bottom:18px'>"
            f"<a href='{escape(article.url)}' style='font-size:17px;font-weight:700;text-decoration:none;color:#111'>{escape(article.title)}</a>"
            f"<div style='color:#667085;margin:5px 0'>{escape(article.source_domain or 'Unknown source')} · {escape(published)}</div>"
            f"<div style='color:#344054'>{escape(article.description[:500])}</div>"
            f"<div style='color:#667085;margin-top:6px'>Matched: {escape(matched)} · Score: {article.relevance_score:.1f}</div>"
            f"</li>"
        )
        text_items.append(
            f"{i}. {article.title}\n"
            f"   {article.source_domain or 'Unknown source'} · {published}\n"
            f"   Matched: {matched} · Score: {article.relevance_score:.1f}\n"
            f"   {article.url}\n"
        )

    html_body = (
        "<html><body style='font-family:Arial,sans-serif;color:#111;line-height:1.5'>"
        f"<h2>NewsRadar</h2><p>{len(items)} new tech stories matched your interests.</p>"
        f"<ol>{''.join(html_items)}</ol>"
        "<p style='color:#98A2B3;font-size:12px'>Personal tech news monitor.</p>"
        "</body></html>"
    )
    return subject, html_body, "NewsRadar\n\n" + "\n".join(text_items)


async def collect_sources() -> tuple[list[dict], dict]:
    all_items: list[dict] = []
    source_status: dict = {}

    official_items, official_status = await fetch_official_sources()
    all_items.extend(official_items)
    source_status["official"] = official_status

    if settings.enable_gdelt:
        try:
            gdelt_items = await fetch_gdelt()
            all_items.extend(gdelt_items)
            source_status["gdelt"] = {"ok": True, "count": len(gdelt_items)}
        except Exception as exc:
            source_status["gdelt"] = {"ok": False, "count": 0, "error": str(exc)[:500]}
            logger.warning("GDELT failed: %s", exc)

    if settings.enable_google_news_rss:
        google_items = await fetch_google_news_rss()
        all_items.extend(google_items)
        source_status["google_news"] = {"ok": True, "count": len(google_items)}

    try:
        community_items = await fetch_community()
        all_items.extend(community_items)
        source_status["hacker_news"] = {"ok": True, "count": len(community_items)}
    except Exception as exc:
        source_status["hacker_news"] = {"ok": False, "count": 0, "error": str(exc)[:500]}

    return all_items, source_status


async def collect_currents() -> tuple[list[dict], dict]:
    try:
        items = await CurrentsClient().latest_news()
        normalized = []
        for item in items:
            categories = item.get("category", [])
            if isinstance(categories, str):
                categories = [categories]
            normalized.append({
                "external_id": item.get("id") or stable_hash(item.get("title", ""), item.get("url", "")),
                "title": item.get("title", ""),
                "description": item.get("description", "") or "",
                "url": item.get("url", ""),
                "author": item.get("author", "") or "",
                "image": item.get("image", "") or "",
                "language": item.get("language", settings.news_language),
                "published_at": item.get("published"),
                "source_domain": __import__("urllib.parse", fromlist=["urlparse"]).urlparse(item.get("url", "")).netloc.lower().removeprefix("www."),
                "source_name": "Currents",
                "source_kind": "aggregator",
                "source_key": "currents",
                "categories": categories,
            })
        return normalized, {"ok": True, "count": len(normalized)}
    except Exception as exc:
        return [], {"ok": False, "count": 0, "error": str(exc)[:500]}


def _normalize_published(value):
    if isinstance(value, datetime):
        return value
    if not value:
        return datetime.now(timezone.utc)
    try:
        from .currents import parse_published
        return parse_published(value) or datetime.now(timezone.utc)
    except Exception:
        return datetime.now(timezone.utc)


async def ingest(items: list[dict], source_status: dict, channel: str, send_email: bool = True) -> dict:
    now = datetime.now(timezone.utc)
    created = 0
    interesting = 0
    pending_created = 0

    with session_scope() as db:
        interests = db.scalars(select(Interest).where(Interest.enabled.is_(True))).all()

        for raw in items:
            title = (raw.get("title") or "").strip()
            url = (raw.get("url") or "").strip()
            if not title or not url:
                continue

            source_kind = raw.get("source_kind", "aggregator")
            categories = raw.get("categories") or raw.get("category") or []
            if isinstance(categories, str):
                categories = [categories]

            if not is_technology(
                title,
                raw.get("description") or "",
                categories=categories,
                source_kind=source_kind,
                source_key=raw.get("source_key", ""),
            ):
                continue

            topic_categories = classify_topics(title, raw.get("description") or "")
            categories = list(dict.fromkeys(["Technology", *[str(x) for x in categories], *topic_categories]))

            external_id = raw.get("external_id") or stable_hash(title, url)
            # Cross-provider de-duplication. Prefer exact external id, then a
            # normalized title hash so the same release isn't repeated by GDELT,
            # Google News and Currents.
            content_hash = title_hash(title)
            existing = db.scalar(
                select(Article).where(
                    or_(Article.external_id == external_id, Article.content_hash == content_hash)
                )
            )
            if existing:
                continue

            result = score_article({**raw, "source_kind": source_kind}, interests)
            article = Article(
                external_id=external_id[:128],
                title=title,
                description=raw.get("description", "") or "",
                url=url,
                author=raw.get("author", "") or "",
                image_url=raw.get("image", "") or "",
                language=raw.get("language", settings.news_language) or settings.news_language,
                categories=json.dumps(categories),
                published_at=_normalize_published(raw.get("published_at")),
                source_domain=raw.get("source_domain", "") or "",
                content_hash=content_hash,
                relevance_score=result.score,
                matched_keywords=json.dumps(result.matched_keywords),
            )
            db.add(article)
            db.flush()
            created += 1

            threshold = float(get_state(db, "notification_threshold", str(settings.notification_threshold)) or settings.notification_threshold)
            if result.score >= threshold:
                db.add(Notification(article_id=article.id, status="pending"))
                interesting += 1
                pending_created += 1

        set_state(db, f"last_{channel}_fetch_at", now.isoformat())
        set_state(db, f"last_{channel}_fetch_status", "ok")
        set_state(db, f"last_{channel}_fetch_error", "")
        set_state(db, "last_fetch_at", now.isoformat())
        set_state(db, "last_fetch_status", "ok")
        set_state(db, "last_fetch_error", "")
        set_state(db, "last_source_status", json.dumps(source_status))

    email_sent, email_error = await send_pending_digest() if send_email and pending_created else (False, "")

    result = {
        "channel": channel,
        "fetched": len(items),
        "created": created,
        "interesting": interesting,
        "email_sent": email_sent,
        "email_error": email_error,
    }
    logger.info("Ingestion result: %s", result)
    return result


async def fetch_official_and_process_news(send_email: bool = True) -> dict:
    items, status = await fetch_official_sources()
    result = await ingest(items, status, "official", send_email=send_email)
    if not send_email:
        return result
    return result


async def fetch_aggregators_and_process_news(send_email: bool = True) -> dict:
    items: list[dict] = []
    status: dict = {}
    if settings.enable_gdelt:
        try:
            gdelt_items = await fetch_gdelt()
            items.extend(gdelt_items)
            status["gdelt"] = {"ok": True, "count": len(gdelt_items)}
        except Exception as exc:
            status["gdelt"] = {"ok": False, "count": 0, "error": str(exc)[:500]}
    if settings.enable_google_news_rss:
        google_items = await fetch_google_news_rss()
        items.extend(google_items)
        status["google_news"] = {"ok": True, "count": len(google_items)}
    if settings.enable_hacker_news:
        try:
            hn_items = await fetch_community()
            items.extend(hn_items)
            status["hacker_news"] = {"ok": True, "count": len(hn_items)}
        except Exception as exc:
            status["hacker_news"] = {"ok": False, "count": 0, "error": str(exc)[:500]}
    if settings.newsapi_api_key:
        try:
            rows = await fetch_newsapi()
            items.extend(rows)
            status["newsapi"] = {"ok": True, "count": len(rows)}
        except Exception as exc:
            status["newsapi"] = {"ok": False, "count": 0, "error": str(exc)[:500]}
    if settings.gnews_api_key:
        try:
            rows = await fetch_gnews()
            items.extend(rows)
            status["gnews"] = {"ok": True, "count": len(rows)}
        except Exception as exc:
            status["gnews"] = {"ok": False, "count": 0, "error": str(exc)[:500]}
    return await ingest(items, status, "aggregators", send_email=send_email)


async def fetch_currents_and_process_news(send_email: bool = True) -> dict:
    items, status = await collect_currents()
    return await ingest(items, {"currents": status}, "currents", send_email=send_email)


async def fetch_and_process_news(send_email: bool = True) -> dict:
    official = await fetch_official_and_process_news(send_email=False)
    aggregators = await fetch_aggregators_and_process_news(send_email=False)
    currents = await fetch_currents_and_process_news(send_email=False)
    research = await fetch_research_and_process_news(send_email=False) if settings.enable_research_collectors else {"channel": "research", "created": 0}
    email_sent, email_error = await send_pending_digest() if send_email else (False, "")
    return {
        "official": official,
        "aggregators": aggregators,
        "currents": currents,
        "research": research,
        "email_sent": email_sent,
        "email_error": email_error,
    }


async def fetch_research_and_process_news(send_email: bool = True) -> dict:
    if not settings.enable_research_collectors:
        return {"channel": "research", "fetched": 0, "created": 0, "papers_created": 0, "news_created": 0, "interesting": 0, "email_sent": False, "email_error": "disabled"}
    items, status = await collect_research()
    result = ingest_research(items)
    now = datetime.now(timezone.utc)
    with session_scope() as db:
        set_state(db, "last_research_fetch_at", now.isoformat())
        set_state(db, "last_research_fetch_status", "ok")
        set_state(db, "last_research_fetch_error", "")
        set_state(db, "last_research_source_status", json.dumps(status))
    email_sent, email_error = await send_pending_digest() if send_email and result.get("interesting") else (False, "")
    result.update({"channel": "research", "email_sent": email_sent, "email_error": email_error})
    logger.info("Research ingestion result: %s", result)
    return result




def application_base_url() -> str:
    configured = settings.app_base_url.strip().rstrip("/")
    if configured:
        return configured
    vercel_url = os.getenv("VERCEL_URL", "").strip().rstrip("/")
    if vercel_url:
        return f"https://{vercel_url}"
    return "http://localhost:8000"


def _morning_digest_subject(now_ist: datetime, count: int) -> str:
    date_text = now_ist.strftime("%a, %d %b %Y")
    if count:
        return f"NewsRadar — Top {count} tech stories · {date_text}"
    return f"NewsRadar — Tech briefing · {date_text}"


def _build_morning_digest(items: list[Article]) -> tuple[str, str, str]:
    ist = ZoneInfo("Asia/Kolkata")
    now_ist = datetime.now(ist)
    base_url = application_base_url()
    matched = []
    for article in items:
        matched.extend(json.loads(article.matched_keywords or "[]"))
    matched = sorted(set(x for x in matched if x), key=str.lower)

    html_items = []
    text_items = []
    for idx, article in enumerate(items, start=1):
        published = article.published_at.astimezone(ist).strftime("%d %b %Y · %I:%M %p IST") if article.published_at else ""
        keywords = ", ".join(json.loads(article.matched_keywords or "[]")) or "technology"
        reader_url = f"{base_url}/read/news/{article.id}"
        source_url = article.url
        html_items.append(
            "<article style='padding:0 0 22px;margin:0 0 22px;border-bottom:1px solid #eaecf0'>"
            f"<div style='font:600 12px/1.4 Arial,sans-serif;color:#667085'>{escape(article.source_domain or 'unknown')} · {escape(published)}</div>"
            f"<h3 style='font:700 19px/1.35 Arial,sans-serif;margin:7px 0 8px;color:#101828'>{idx}. {escape(article.title)}</h3>"
            f"<p style='font:14px/1.6 Arial,sans-serif;color:#344054;margin:0 0 10px'>{escape((article.description or '')[:700])}</p>"
            f"<div style='font:12px/1.4 Arial,sans-serif;color:#667085;margin-bottom:10px'>Matched: {escape(keywords)} · Relevance {article.relevance_score:.1f}</div>"
            f"<a href='{escape(reader_url)}' style='display:inline-block;padding:8px 12px;border:1px solid #d0d5dd;border-radius:7px;text-decoration:none;color:#111827;font:600 13px Arial,sans-serif;margin-right:6px'>Read in NewsRadar</a>"
            f"<a href='{escape(source_url)}' style='display:inline-block;padding:8px 12px;text-decoration:none;color:#344054;font:600 13px Arial,sans-serif'>Original source ↗</a>"
            "</article>"
        )
        text_items.append(
            f"{idx}. {article.title}\n"
            f"   {article.source_domain or 'unknown'} · {published}\n"
            f"   Matched: {keywords} · Relevance: {article.relevance_score:.1f}\n"
            f"   Read: {reader_url}\n"
            f"   Original: {source_url}\n"
        )

    if items:
        intro = f"Top {len(items)} stories selected from your enabled interests."
        interest_line = f"Selected interests: {', '.join(matched[:12])}" if matched else "Selected interests: technology"
    else:
        intro = "No new high-relevance technology stories matched your enabled interests in the selected window."
        interest_line = "No matching topics detected."

    html_body = (
        "<html><body style='margin:0;background:#f8fafc'>"
        "<div style='max-width:680px;margin:0 auto;padding:28px 18px;font-family:Arial,sans-serif;color:#101828'>"
        "<div style='font-size:12px;font-weight:700;letter-spacing:.08em;color:#667085'>NEWSRADAR · MORNING BRIEF</div>"
        f"<h1 style='font-size:26px;line-height:1.2;margin:8px 0'>Your technology briefing</h1>"
        f"<p style='font-size:14px;line-height:1.6;color:#475467;margin:0 0 6px'>{escape(intro)}</p>"
        f"<p style='font-size:12px;line-height:1.5;color:#667085;margin:0 0 24px'>{escape(interest_line)} · {escape(now_ist.strftime('%d %b %Y · 9:00 AM IST'))}</p>"
        f"{''.join(html_items)}"
        f"<p style='font-size:11px;line-height:1.5;color:#98a2b3;margin-top:18px'>NewsRadar combines first-party technical sources, broad news discovery and research feeds. Coverage is best-effort because publishers can rate-limit or delay public feeds.</p>"
        "</div></body></html>"
    )
    text_body = "NewsRadar — Morning Brief\n\n" + intro + "\n" + interest_line + "\n\n" + ("\n".join(text_items) if text_items else "No matching stories.")
    return _morning_digest_subject(now_ist, len(items)), html_body, text_body


def send_morning_digest(force: bool = False) -> dict:
    """Send one daily top-five technology briefing using the currently enabled interests.

    The candidate stories are re-scored at send time, so changing favourites in
    Settings immediately changes tomorrow's briefing without requiring a full
    re-ingestion of the database.
    """
    ist = ZoneInfo("Asia/Kolkata")
    now_utc = datetime.now(timezone.utc)
    today_ist = now_utc.astimezone(ist).date().isoformat()

    with session_scope() as db:
        previous_digest_at = get_state(db, "last_morning_digest_at", "")
        previous_date = get_state(db, "last_morning_digest_date", "")
        if previous_date == today_ist and not force:
            return {"sent": False, "skipped": True, "reason": "already_sent_today", "count": 0}

        token = gmail_token(db)
        to = recipient(db)
        if not token:
            return {"sent": False, "skipped": False, "reason": "gmail_not_connected", "count": 0}
        if not to:
            return {"sent": False, "skipped": False, "reason": "recipient_not_configured", "count": 0}

        if previous_digest_at:
            try:
                since = datetime.fromisoformat(previous_digest_at.replace("Z", "+00:00"))
                if since.tzinfo is None:
                    since = since.replace(tzinfo=timezone.utc)
            except ValueError:
                since = now_utc - timedelta(hours=settings.morning_digest_hours)
        else:
            since = now_utc - timedelta(hours=settings.morning_digest_hours)

        interests = db.scalars(select(Interest).where(Interest.enabled.is_(True))).all()
        if not interests:
            return {"sent": False, "skipped": False, "reason": "no_enabled_interests", "count": 0}

        # Look at a reasonably large recent pool, then re-score against current
        # interests. This is what makes the digest reflect the user's latest choices.
        recent = list(db.scalars(
            select(Article)
            .where(
                Article.is_hidden.is_(False),
                func.coalesce(Article.published_at, Article.created_at) >= since,
            )
            .order_by(func.coalesce(Article.published_at, Article.created_at).desc())
            .limit(500)
        ).all())

        ranked: list[tuple[float, datetime, Article, list[str]]] = []
        for article in recent:
            result = score_article(
                {
                    "title": article.title,
                    "description": article.description,
                    "source_kind": "official" if article.source_domain in OFFICIAL_DOMAINS else "aggregator",
                },
                interests,
            )
            # Require an actual interest match. The official-source bonus by itself
            # must never qualify a story for the personalized briefing.
            if not result.matched_keywords or result.score <= 0:
                continue
            published = article.published_at or article.created_at or now_utc
            ranked.append((result.score, published, article, result.matched_keywords))

        ranked.sort(key=lambda x: (x[0], x[1]), reverse=True)
        target = max(settings.morning_digest_limit, 1)
        selected = [row[2] for row in ranked[:target]]

        subject, html_body, text_body = _build_morning_digest(selected)

        try:
            message_id = GmailService.send_email(token, to, subject, html_body, text_body)
            sent_at = datetime.now(timezone.utc)
            selected_ids = {article.id for article in selected}
            for article_id in selected_ids:
                note = db.scalar(select(Notification).where(Notification.article_id == article_id))
                if note and note.status == "pending":
                    note.status = "sent"
                    note.sent_at = sent_at
                    note.error_message = ""

            set_state(db, "last_morning_digest_at", sent_at.isoformat())
            set_state(db, "last_morning_digest_date", today_ist)
            set_state(db, "last_morning_digest_status", "sent")
            set_state(db, "last_morning_digest_error", "")
            set_state(db, "last_morning_digest_message_id", message_id)
            db.commit()
            return {
                "sent": True,
                "skipped": False,
                "count": len(selected),
                "message_id": message_id,
                "scheduled_for": "09:00 Asia/Kolkata",
            }
        except Exception as exc:
            error = str(exc)[:1000]
            set_state(db, "last_morning_digest_status", "error")
            set_state(db, "last_morning_digest_error", error)
            db.commit()
            return {
                "sent": False,
                "skipped": False,
                "reason": "send_failed",
                "count": len(selected),
                "error": error,
            }


async def send_pending_digest() -> tuple[bool, str]:
    with session_scope() as db:
        token = gmail_token(db)
        to = recipient(db)
        if not token:
            return False, "Gmail is not connected."
        if not to:
            return False, "No notification recipient configured."

        notifications = db.scalars(
            select(Notification)
            .where(Notification.status == "pending")
            .order_by(Notification.created_at.asc())
            .limit(30)
        ).all()
        research_notifications = db.scalars(
            select(ResearchNotification)
            .where(ResearchNotification.status == "pending")
            .order_by(ResearchNotification.created_at.asc())
            .limit(30)
        ).all()
        articles = [n.article for n in notifications]
        papers = [n for n in research_notifications if n.research_item]
        if not articles and not papers:
            return False, ""

        html_items = []
        text_items = []
        number = 0
        for article in articles:
            number += 1
            matched = ", ".join(json.loads(article.matched_keywords or "[]")) or "technology"
            published = article.published_at.astimezone(timezone.utc).strftime("%d %b %Y, %H:%M UTC") if article.published_at else ""
            html_items.append(
                f"<li style='margin-bottom:18px'><a href='{escape(article.url)}' style='font-size:17px;font-weight:700;text-decoration:none;color:#111'>{escape(article.title)}</a>"
                f"<div style='color:#667085;margin:5px 0'>{escape(article.source_domain or 'Unknown source')} · {escape(published)}</div>"
                f"<div style='color:#344054'>{escape(article.description[:500])}</div>"
                f"<div style='color:#667085;margin-top:6px'>Matched: {escape(matched)} · Score: {article.relevance_score:.1f}</div></li>"
            )
            text_items.append(f"{number}. {article.title}\n   {article.source_domain or 'Unknown source'} · {published}\n   {article.url}\n")
        for n in papers:
            number += 1
            paper = n.research_item
            matches = ", ".join(json.loads(paper.matched_keywords or "[]")) or "research"
            published = paper.published_at.astimezone(timezone.utc).strftime("%d %b %Y, %H:%M UTC") if paper.published_at else ""
            link = paper.pdf_url or paper.landing_url
            html_items.append(
                f"<li style='margin-bottom:18px'><a href='{escape(link)}' style='font-size:17px;font-weight:700;text-decoration:none;color:#111'>{escape(paper.title)}</a>"
                f"<div style='color:#667085;margin:5px 0'>{escape(paper.source)} · {escape(published)}</div>"
                f"<div style='color:#344054'>{escape((paper.abstract or '')[:500])}</div>"
                f"<div style='color:#667085;margin-top:6px'>Research paper · Matched: {escape(matches)} · Score: {paper.relevance_score:.1f}</div></li>"
            )
            text_items.append(f"{number}. {paper.title}\n   {paper.source} · {published}\n   {link}\n")

        subject = f"NewsRadar — {number} new tech and research items"
        html_body = (
            "<html><body style='font-family:Arial,sans-serif;color:#111;line-height:1.5'>"
            f"<h2>NewsRadar</h2><p>{number} new items matched your interests.</p><ol>{''.join(html_items)}</ol>"
            "<p style='color:#98A2B3;font-size:12px'>Personal technology and research monitor.</p></body></html>"
        )
        text_body = "NewsRadar\n\n" + "\n".join(text_items)
        try:
            GmailService.send_email(token, to, subject, html_body, text_body)
            sent_at = datetime.now(timezone.utc)
            for notification in notifications:
                notification.status = "sent"
                notification.sent_at = sent_at
                notification.error_message = ""
            for notification in research_notifications:
                notification.status = "sent"
                notification.sent_at = sent_at
                notification.error_message = ""
            set_state(db, "last_email_at", sent_at.isoformat())
            set_state(db, "last_email_status", "sent")
            db.commit()
            return True, ""
        except Exception as exc:
            error = str(exc)[:1000]
            for notification in notifications:
                notification.error_message = error
            for notification in research_notifications:
                notification.error_message = error
            set_state(db, "last_email_status", "error")
            set_state(db, "last_email_error", error)
            db.commit()
            return False, error

