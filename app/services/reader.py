from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

try:
    import trafilatura
except ImportError:  # pragma: no cover
    trafilatura = None


@dataclass
class ExtractedArticle:
    title: str = ""
    author: str = ""
    published: datetime | None = None
    description: str = ""
    image_url: str = ""
    canonical_url: str = ""
    paragraphs: list[str] | None = None
    blocked: bool = False


def _clean(text: str | None) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _meta(soup: BeautifulSoup, *names: str) -> str:
    for name in names:
        node = soup.find("meta", attrs={"name": name}) or soup.find("meta", attrs={"property": name})
        if node and node.get("content"):
            return _clean(node.get("content"))
    return ""


def _extract_paragraphs(soup: BeautifulSoup) -> list[str]:
    for selector in (
        "article",
        "main",
        '[role="main"]',
        ".article-body",
        ".post-content",
        ".entry-content",
    ):
        root = soup.select_one(selector)
        if root:
            paragraphs = [_clean(p.get_text(" ", strip=True)) for p in root.select("p")]
            paragraphs = [p for p in paragraphs if len(p) >= 30]
            if paragraphs:
                return paragraphs[:300]
    return []


def _trafilatura_extract(html: str, url: str) -> tuple[str, str, list[str]]:
    if trafilatura is None:
        return "", "", []
    try:
        data = trafilatura.extract(
            html,
            include_comments=False,
            include_tables=False,
            include_links=False,
            favor_precision=False,
            favor_recall=True,
            output_format="json",
            url=url,
        )
        if not data:
            return "", "", []
        import json
        payload = json.loads(data)
        title = _clean(payload.get("title"))
        author = _clean(payload.get("author"))
        text = payload.get("text") or ""
        paragraphs = [_clean(x) for x in re.split(r"\n\s*\n|\n", text) if _clean(x)]
        return title, author, [x for x in paragraphs if len(x) >= 20][:300]
    except Exception:
        return "", "", []


def extract_article(url: str) -> ExtractedArticle:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/140 Safari/537.36 NewsRadar/4.0"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.8",
    }

    with httpx.Client(timeout=25.0, follow_redirects=True, headers=headers) as client:
        response = client.get(url)
        response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    canonical = ""
    canonical_node = soup.find("link", rel="canonical")
    if canonical_node and canonical_node.get("href"):
        canonical = urljoin(url, canonical_node["href"])

    image = _meta(soup, "og:image", "twitter:image")
    title = _meta(soup, "og:title", "twitter:title") or _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    description = _meta(soup, "description", "og:description", "twitter:description")
    author = _meta(soup, "author", "article:author")

    published = None
    date_text = _meta(soup, "article:published_time", "datePublished", "date")
    if not date_text:
        time_node = soup.find("time", attrs={"datetime": True})
        date_text = time_node.get("datetime", "") if time_node else ""
    if date_text:
        try:
            published = datetime.fromisoformat(date_text.replace("Z", "+00:00"))
        except ValueError:
            published = None

    t_title, t_author, paragraphs = _trafilatura_extract(response.text, url)
    if t_title:
        title = t_title
    if t_author:
        author = t_author
    if not paragraphs:
        paragraphs = _extract_paragraphs(soup)

    # Remove obvious navigation/boilerplate from the fallback extraction.
    paragraphs = [
        p for p in paragraphs
        if not any(marker in p.lower() for marker in (
            "subscribe to our newsletter",
            "sign up for our newsletter",
            "accept cookies",
        ))
    ]

    blocked = response.status_code in {401, 403, 451} or not paragraphs

    return ExtractedArticle(
        title=title,
        author=author,
        published=published,
        description=description,
        image_url=image,
        canonical_url=canonical or str(response.url),
        paragraphs=paragraphs,
        blocked=blocked,
    )
