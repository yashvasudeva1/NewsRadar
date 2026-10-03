from __future__ import annotations

import email.utils
import hashlib
import json
import logging
import asyncio
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, urlparse

import feedparser
import httpx
from bs4 import BeautifulSoup

from ..config import settings
from .sources import AGGREGATOR_QUERIES, GOOGLE_NEWS_QUERIES, OFFICIAL_SOURCES, SourceDefinition

logger = logging.getLogger(__name__)


class SourceFetchError(RuntimeError):
    pass


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", BeautifulSoup(value, "html.parser").get_text(" ", strip=True)).strip()


def parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = email.utils.parsedate_to_datetime(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        pass
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def stable_external_id(source_key: str, url: str, title: str) -> str:
    raw = f"{source_key}|{url.strip().lower()}|{title.strip().lower()}".encode("utf-8")
    return f"{source_key}:{hashlib.sha1(raw).hexdigest()}"


def normalize_item(
    *,
    source_key: str,
    source_name: str,
    kind: str,
    title: str,
    url: str,
    description: str = "",
    author: str = "",
    published_at: datetime | None = None,
    image_url: str = "",
    language: str = "en",
) -> dict:
    url = urljoin(url, url) if url else ""
    return {
        "external_id": stable_external_id(source_key, url, title),
        "title": clean_text(title),
        "description": clean_text(description),
        "url": url,
        "author": clean_text(author),
        "image": image_url,
        "language": language or "en",
        "published_at": published_at or datetime.now(timezone.utc),
        "source_domain": urlparse(url).netloc.lower().removeprefix("www."),
        "source_name": source_name,
        "source_kind": kind,
        "source_key": source_key,
    }


async def fetch_rss(source: SourceDefinition, client: httpx.AsyncClient, url: str | None = None) -> list[dict]:
    response = await client.get(url or source.url, headers=dict(source.headers), follow_redirects=True)
    response.raise_for_status()
    parsed = feedparser.parse(response.content)

    result: list[dict] = []
    for entry in parsed.entries[:80]:
        title = entry.get("title", "")
        link = entry.get("link", "")
        if not title or not link:
            continue

        published = None
        for key in ("published", "updated", "created"):
            published = parse_date(entry.get(key))
            if published:
                break
        if not published and entry.get("published_parsed"):
            try:
                import calendar
                published = datetime.fromtimestamp(calendar.timegm(entry.published_parsed), tz=timezone.utc)
            except Exception:
                published = None

        author = entry.get("author", "")
        summary = entry.get("summary", "") or entry.get("description", "")
        result.append(
            normalize_item(
                source_key=source.key,
                source_name=source.name,
                kind=source.kind,
                title=title,
                url=link,
                description=summary,
                author=author,
                published_at=published,
                image_url="",
            )
        )
    return result


def _looks_like_article_url(url: str, source: SourceDefinition) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return False
    path = parsed.path.rstrip("/")
    if not path or len(path) < 5:
        return False

    if source.key == "openai":
        return "/index/" in path
    if source.key == "anthropic":
        return re.search(r"^/news/[^/]+/?$", path) is not None
    if source.key == "google-ai":
        return "/innovation-and-ai/" in path and path.count("/") >= 5
    if source.key in {"google-deepmind", "google-research"}:
        return re.search(r"^/blog/[^/]+/?$", path) is not None or ("/blog/" in path and path.count("/") >= 3)
    if source.key == "google-dev":
        return path.startswith("/en/") and path.count("/") >= 2
    if source.key in {"microsoft", "microsoft-research"}:
        return "/blog/" in path and path.count("/") >= 4
    if source.key in {"microsoft-dev", "azure"}:
        return "/blog/" in path and path.count("/") >= 3 and not any(x in path for x in ("/category/", "/tag/", "/author/"))
    if source.key in {"meta-ai", "cohere", "mistral", "xai"}:
        return re.search(r"/(?:blog|news)/[^/]+/?$", path) is not None
    if source.key == "nvidia":
        return re.search(r"^/blog/[^/]+/?$", path) is not None and "/category/" not in path
    if source.key == "github":
        return re.search(r"^/changelog/\d{4}-\d{2}-\d{2}-", path) is not None
    if source.key == "github-blog":
        return path not in {"", "/"} and not any(x in path for x in ("/category/", "/tag/", "/author/", "/topics/", "/search", "/company/"))
    if source.key == "aws-ml":
        return "/blogs/machine-learning/" in path and path.count("/") >= 4
    if source.key == "apple":
        return re.search(r"^/newsroom/\d{4}/\d{2}/[^/]+/?$", path) is not None
    if source.key == "apple-ml":
        return "/research/" in path and path.count("/") >= 3
    if source.key == "kubernetes":
        return re.search(r"^/blog/\d{4}/", path) is not None or ("/blog/" in path and path.count("/") >= 4)
    if source.key == "docker":
        return re.search(r"^/blog/[^/]+/?$", path) is not None
    if source.key == "huggingface":
        return re.search(r"^/blog/[^/]+/?$", path) is not None
    if source.key == "supabase":
        return re.search(r"^/blog/[^/]+/?$", path) is not None
    if source.key == "vercel":
        return re.search(r"^/changelog/[^/]+/?$", path) is not None
    if source.key == "jetbrains":
        return path.count("/") >= 3 and not any(x in path for x in ("/category/", "/tag/", "/author/"))
    if source.key == "mozilla":
        return path.count("/") >= 3 and not any(x in path for x in ("/category/", "/tag/", "/author/"))
    if source.key == "chromium":
        return path.count("/") >= 2 and not any(x in path for x in ("/search", "/feeds/"))
    if source.key == "amd":
        return "newsroom" in path and len(path.split("/")) >= 5
    if source.key == "intel":
        return "/newsroom/" in path and path.count("/") >= 6
    if source.key == "qualcomm":
        return re.search(r"^/news/releases/[^/]+/?$", path) is not None
    if source.key == "samsung":
        return re.search(r"^/global/[^/]+/?$", path) is not None
    return any(hint in path for hint in source.article_path_hints)


def _container_for_anchor(anchor):
    for parent_name in ("article", "li", "section", "div"):
        parent = anchor.find_parent(parent_name)
        if parent:
            return parent
    return anchor.parent


def parse_html_listing(source: SourceDefinition, html: str, limit: int = 40) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    result: list[dict] = []
    seen: set[str] = set()

    # Prefer <article> blocks, but fall back to anchors for modern JS-light pages.
    anchors = soup.select("article a[href], main a[href], a[href]")
    for anchor in anchors:
        href = anchor.get("href", "")
        url = urljoin(source.url, href)
        if not _looks_like_article_url(url, source):
            continue
        if url in seen:
            continue

        title = clean_text(anchor.get_text(" ", strip=True) or anchor.get("aria-label") or anchor.get("title"))
        if len(title) < 8 or title.lower() in {"read more", "learn more", "see all", "view all"}:
            continue

        box = _container_for_anchor(anchor)
        description = ""
        author = ""
        image_url = ""
        published = None

        if box:
            paragraph = box.find("p")
            if paragraph:
                description = clean_text(paragraph.get_text(" ", strip=True))
            author_el = box.find(attrs={"rel": "author"}) or box.find(class_=re.compile("author", re.I))
            if author_el:
                author = clean_text(author_el.get_text(" ", strip=True))
            img = box.find("img")
            if img:
                image_url = img.get("src") or img.get("data-src") or ""
                image_url = urljoin(url, image_url)
            time_el = box.find("time")
            if time_el:
                published = parse_date(time_el.get("datetime") or time_el.get_text(" ", strip=True))

        if not published:
            # Try a nearby textual date without scraping individual article pages.
            blob = clean_text(box.get_text(" ", strip=True) if box else "")
            match = re.search(r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4}", blob, flags=re.I)
            if match:
                published = parse_date(match.group(0))

        result.append(
            normalize_item(
                source_key=source.key,
                source_name=source.name,
                kind=source.kind,
                title=title,
                url=url,
                description=description,
                author=author,
                published_at=published,
                image_url=image_url,
            )
        )
        seen.add(url)
        if len(result) >= limit:
            break
    return result


async def fetch_html(source: SourceDefinition, client: httpx.AsyncClient) -> list[dict]:
    response = await client.get(source.url, headers=dict(source.headers), follow_redirects=True)
    response.raise_for_status()
    return parse_html_listing(source, response.text)


def discover_feed_url(source_url: str, html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for link in soup.find_all("link", href=True):
        rel = {str(x).lower() for x in (link.get("rel") or [])}
        typ = str(link.get("type") or "").lower()
        href = urljoin(source_url, link.get("href"))
        if "alternate" in rel and typ in {"application/rss+xml", "application/atom+xml", "application/xml", "text/xml"}:
            return href
    # A few WordPress properties expose a feed at /feed/ even without an
    # explicit alternate link. Do not force it if the site is not WordPress.
    parsed = urlparse(source_url)
    if "wordpress" in html.lower() and parsed.path.rstrip("/") in {"", "/blog"}:
        return urljoin(source_url, "feed/")
    return ""


async def fetch_auto(source: SourceDefinition, client: httpx.AsyncClient) -> list[dict]:
    response = await client.get(source.url, headers=dict(source.headers), follow_redirects=True)
    response.raise_for_status()
    feed_url = discover_feed_url(str(response.url), response.text)
    if feed_url:
        try:
            return await fetch_rss(source, client, feed_url)
        except Exception as exc:
            logger.debug("Feed discovery failed for %s: %s", source.name, exc)
    return parse_html_listing(source, response.text)


async def fetch_official_sources() -> tuple[list[dict], dict]:
    items: list[dict] = []
    status: dict = {}
    semaphore = asyncio.Semaphore(6)

    async with httpx.AsyncClient(timeout=25.0) as client:
        async def one(source: SourceDefinition):
            async with semaphore:
                try:
                    batch = await (fetch_rss(source, client) if source.mode == "rss" else fetch_html(source, client) if source.mode == "html" else fetch_auto(source, client))
                    return source.key, {"name": source.name, "ok": True, "count": len(batch)}, batch
                except Exception as exc:
                    logger.warning("Official source failed %s: %s", source.name, exc)
                    return source.key, {"name": source.name, "ok": False, "error": str(exc)[:500], "count": 0}, []

        results = await asyncio.gather(*(one(source) for source in OFFICIAL_SOURCES))

    for key, source_status, batch in results:
        status[key] = source_status
        items.extend(batch)
    return items, status


async def fetch_gdelt() -> list[dict]:
    if not settings.enable_gdelt:
        return []
    query = AGGREGATOR_QUERIES[0]
    params = {
        "query": f"({query})",
        "mode": "artlist",
        "format": "json",
        "timespan": settings.gdelt_timespan,
        "maxrecords": min(max(settings.gdelt_maxrecords, 1), 250),
        "sort": "datedesc",
    }
    async with httpx.AsyncClient(timeout=25.0) as client:
        response = await client.get("https://api.gdeltproject.org/api/v2/doc/doc", params=params)
        response.raise_for_status()
        payload = response.json()

    result: list[dict] = []
    for row in payload.get("articles", []) or []:
        title = row.get("title", "")
        url = row.get("url", "")
        if not title or not url:
            continue
        published = None
        if row.get("seendate"):
            try:
                published = datetime.strptime(row["seendate"], "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
            except ValueError:
                pass
        result.append(
            normalize_item(
                source_key="gdelt",
                source_name=row.get("domain") or "GDELT",
                kind="aggregator",
                title=title,
                url=url,
                description="",
                published_at=published,
                image_url=row.get("socialimage", "") or "",
                language=row.get("language", "en") or "en",
            )
        )
    return result


async def fetch_google_news_rss() -> list[dict]:
    if not settings.enable_google_news_rss:
        return []

    async with httpx.AsyncClient(timeout=20.0) as client:
        async def one(query: str) -> list[dict]:
            output: list[dict] = []
            params = {
                "q": f"({query}) {settings.google_news_window}",
                "hl": "en-IN",
                "gl": "IN",
                "ceid": "IN:en",
            }
            try:
                response = await client.get(
                    "https://news.google.com/rss/search",
                    params=params,
                    headers={"User-Agent": "NewsRadar/2.0"},
                )
                response.raise_for_status()
                parsed = feedparser.parse(response.content)
                for entry in parsed.entries[:40]:
                    title = entry.get("title", "")
                    link = entry.get("link", "")
                    if not title or not link:
                        continue
                    published = parse_date(entry.get("published")) or parse_date(entry.get("updated"))
                    source_name = "Google News"
                    source_domain = "news.google.com"
                    description = entry.get("summary", "") or entry.get("description", "")
                    if hasattr(entry, "source") and entry.source:
                        source_name = entry.source.get("title", "Google News")
                        source_link = entry.source.get("href") or entry.source.get("link") or ""
                        if source_link:
                            source_domain = urlparse(source_link).netloc.lower().removeprefix("www.") or source_domain
                    item = normalize_item(
                        source_key="google-news",
                        source_name=source_name,
                        kind="aggregator",
                        title=title,
                        url=link,
                        description=description,
                        published_at=published,
                    )
                    item["source_domain"] = source_domain
                    output.append(item)
            except Exception as exc:
                logger.warning("Google News RSS query failed: %s", exc)
            return output

        batches = await asyncio.gather(*(one(query) for query in GOOGLE_NEWS_QUERIES))

    result: list[dict] = []
    for batch in batches:
        result.extend(batch)
    return result


async def fetch_newsapi() -> list[dict]:
    if not settings.newsapi_api_key:
        return []
    params = {
        "q": '(AI OR "artificial intelligence" OR LLM OR "machine learning" OR robotics OR cybersecurity OR semiconductor OR GPU OR developer OR "open source")',
        "language": "en",
        "sortBy": "publishedAt",
        "pageSize": 100,
        "apiKey": settings.newsapi_api_key,
    }
    async with httpx.AsyncClient(timeout=25.0) as client:
        response = await client.get("https://newsapi.org/v2/everything", params=params)
        response.raise_for_status()
        payload = response.json()
    result=[]
    for row in payload.get("articles", []) or []:
        title = row.get("title") or ""
        url = row.get("url") or ""
        if not title or not url:
            continue
        result.append(normalize_item(
            source_key="newsapi", source_name="NewsAPI", kind="aggregator",
            title=title, url=url, description=row.get("description") or "",
            author=row.get("author") or "", published_at=parse_date(row.get("publishedAt")),
            image_url=row.get("urlToImage") or "",
        ))
    return result


async def fetch_gnews() -> list[dict]:
    if not settings.gnews_api_key:
        return []
    params = {
        "q": 'AI OR "artificial intelligence" OR LLM OR "machine learning" OR robotics OR cybersecurity OR semiconductor OR GPU',
        "lang": "en",
        "max": 100,
        "sortby": "publishedAt",
        "apikey": settings.gnews_api_key,
    }
    async with httpx.AsyncClient(timeout=25.0) as client:
        response = await client.get("https://gnews.io/api/v4/search", params=params)
        response.raise_for_status()
        payload = response.json()
    result=[]
    for row in payload.get("articles", []) or []:
        title = row.get("title") or ""
        url = row.get("url") or ""
        if not title or not url:
            continue
        source = row.get("source") or {}
        result.append(normalize_item(
            source_key="gnews", source_name=source.get("name") or "GNews", kind="aggregator",
            title=title, url=url, description=row.get("description") or row.get("content") or "",
            author="", published_at=parse_date(row.get("publishedAt")), image_url="",
        ))
    return result


async def fetch_community() -> list[dict]:
    # Hacker News' public Algolia endpoint is a useful developer pulse,
    # intentionally kept separate from official/reported news.
    params = {
        "query": "AI OR LLM OR OpenAI OR NVIDIA OR developer OR cloud OR cybersecurity",
        "tags": "story",
        "hitsPerPage": 60,
    }
    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.get("https://hn.algolia.com/api/v1/search_by_date", params=params)
        response.raise_for_status()
        payload = response.json()

    result: list[dict] = []
    for hit in payload.get("hits", []) or []:
        title = hit.get("title") or ""
        url = hit.get("url") or (f"https://news.ycombinator.com/item?id={hit.get('objectID')}" if hit.get("objectID") else "")
        if not title or not url:
            continue
        created = hit.get("created_at")
        published = parse_date(created)
        result.append(
            normalize_item(
                source_key="hacker-news",
                source_name="Hacker News",
                kind="community",
                title=title,
                url=url,
                published_at=published,
            )
        )
    return result
