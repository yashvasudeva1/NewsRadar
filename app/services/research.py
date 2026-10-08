from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode, urlparse

import feedparser
import httpx
from bs4 import BeautifulSoup

from ..config import settings
from ..db import session_scope
from ..models import Interest, ResearchItem, ResearchNotification
from .filtering import normalize

logger = logging.getLogger(__name__)

ARXIV_QUERY = (
    "cat:cs.AI OR cat:cs.CL OR cat:cs.LG OR cat:cs.CV OR cat:cs.NE OR "
    "cat:cs.RO OR cat:cs.CR OR cat:cs.DC OR cat:cs.SE OR cat:cs.PL OR "
    "cat:stat.ML OR cat:cs.IR OR cat:cs.HC"
)

RESEARCH_TERMS = (
    "artificial intelligence", "ai", "machine learning", "deep learning", "llm",
    "large language model", "foundation model", "agent", "agents", "transformer",
    "computer vision", "multimodal", "robotics", "reinforcement learning", "nlp",
    "natural language", "generative ai", "diffusion", "reasoning", "rlhf", "alignment",
    "retrieval augmented generation", "rag", "cybersecurity", "quantum", "semiconductor",
    "software engineering", "developer tools", "distributed systems", "database",
)

RESEARCH_NEWS_QUERIES = (
    '"research paper" AI OR LLM OR "machine learning"',
    '"new paper" AI OR LLM OR robotics OR cybersecurity',
    'AI research announcement OR study OR benchmark',
    'site:research.google AI research paper',
    'site:microsoft.com/en-us/research AI research paper',
    'site:ai.meta.com/research AI paper',
    'site:research.nvidia.com AI research',
)

RESEARCH_BLOGS = (
    ("openai", "OpenAI", "https://openai.com/news/", ("/index/",)),
    ("anthropic", "Anthropic", "https://www.anthropic.com/news", ("/news/",)),
    ("google-research", "Google Research", "https://research.google/blog/", ("/blog/",)),
    ("google-deepmind", "Google DeepMind", "https://deepmind.google/blog/", ("/blog/",)),
    ("microsoft-research", "Microsoft Research", "https://www.microsoft.com/en-us/research/blog/", ("/en-us/research/blog/",)),
    ("meta-ai", "Meta AI", "https://ai.meta.com/blog/", ("/blog/",)),
    ("nvidia", "NVIDIA Research", "https://developer.nvidia.com/blog/tag/nvidia-research/", ("/blog/",)),
    ("apple-ml", "Apple Machine Learning Research", "https://machinelearning.apple.com/", ("/research/",)),
    ("mistral", "Mistral AI", "https://mistral.ai/news/", ("/news/",)),
    ("cohere", "Cohere", "https://cohere.com/blog", ("/blog/",)),
)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def parse_any_date(value) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def clean(value: str | None) -> str:
    return " ".join(BeautifulSoup(value or "", "html.parser").get_text(" ", strip=True).split())


def _contains_term(text: str, term: str) -> bool:
    term = normalize(term)
    if not term:
        return False
    if len(term) <= 3 and " " not in term:
        import re
        return re.search(rf"\b{re.escape(term)}\b", text) is not None
    return term in text


def is_research_tech(title: str, abstract: str = "", categories: list[str] | None = None) -> bool:
    text = normalize(f"{title} {abstract} {' '.join(categories or [])}")
    return any(_contains_term(text, term) for term in RESEARCH_TERMS)


def topic_labels(title: str, abstract: str) -> list[str]:
    text = normalize(f"{title} {abstract}")
    mapping = {
        "AI": ("artificial intelligence", " ai ", "agent", "llm", "language model", "generative"),
        "Machine Learning": ("machine learning", "deep learning", "reinforcement learning", "rlhf"),
        "NLP": ("nlp", "natural language", "language model", "translation", "text"),
        "Computer Vision": ("computer vision", "visual", "image", "video", "vision"),
        "Robotics": ("robot", "robotics", "humanoid", "embodied"),
        "Security": ("cybersecurity", "security", "vulnerability", "privacy", "cryptography"),
        "Systems": ("distributed", "database", "systems", "cloud", "compiler", "software engineering"),
        "Quantum": ("quantum", "qubit"),
        "Chips": ("semiconductor", "gpu", "tpu", "accelerator", "chip", "hardware"),
    }
    labels = ["Research"]
    for label, terms in mapping.items():
        if any(term in text for term in terms):
            labels.append(label)
    return labels


def score_paper(title: str, abstract: str, interests: list[Interest]):
    title_n, abstract_n = normalize(title), normalize(abstract)
    score = 0.0
    matches: list[str] = []
    for interest in interests:
        if not interest.enabled:
            continue
        key = normalize(interest.keyword)
        if not key:
            continue
        if _contains_term(title_n, key):
            score += 5.0 * interest.weight
            matches.append(interest.keyword)
        elif _contains_term(abstract_n, key):
            score += 2.0 * interest.weight
            matches.append(interest.keyword)
    return round(score, 2), sorted(set(matches), key=str.lower)


async def fetch_arxiv() -> list[dict]:
    params = {
        "search_query": ARXIV_QUERY,
        "start": 0,
        "max_results": min(max(settings.arxiv_max_results, 1), 100),
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    }
    url = "https://export.arxiv.org/api/query?" + urlencode(params)
    async with httpx.AsyncClient(timeout=30.0, headers={"User-Agent": "NewsRadar/3.0 (personal research reader)"}) as client:
        response = await client.get(url)
        response.raise_for_status()
    feed = feedparser.parse(response.content)
    items: list[dict] = []
    for entry in feed.entries:
        title = clean(entry.get("title"))
        abstract = clean(entry.get("summary"))
        if not title or not is_research_tech(title, abstract):
            continue
        arxiv_id = str(entry.get("id", "")).rstrip("/").split("/abs/")[-1]
        published = parse_any_date(entry.get("published"))
        updated = parse_any_date(entry.get("updated"))
        authors = [clean(a.get("name")) for a in entry.get("authors", []) if a.get("name")]
        categories = [str(t.get("term")) for t in entry.get("tags", []) if t.get("term")]
        landing = entry.get("link") or f"https://arxiv.org/abs/{arxiv_id}"
        pdf = f"https://arxiv.org/pdf/{arxiv_id}" if arxiv_id else ""
        items.append({
            "external_id": f"arxiv:{arxiv_id}",
            "item_type": "paper",
            "title": title,
            "abstract": abstract,
            "authors": authors,
            "categories": list(dict.fromkeys(topic_labels(title, abstract) + categories)),
            "source": "arXiv",
            "source_domain": "arxiv.org",
            "venue": "arXiv",
            "paper_id": arxiv_id,
            "landing_url": landing,
            "pdf_url": pdf,
            "published_at": published,
            "updated_at": updated,
            "citation_count": 0,
            "doi": "",
        })
    return items


async def fetch_huggingface_daily() -> list[dict]:
    url = "https://huggingface.co/api/daily_papers"
    params = {"limit": min(max(settings.huggingface_papers_limit, 1), 100), "sort": "publishedAt"}
    headers = {"User-Agent": "NewsRadar/3.0"}
    if getattr(settings, "huggingface_token", ""):
        headers["Authorization"] = f"Bearer {settings.huggingface_token}"
    async with httpx.AsyncClient(timeout=25.0, headers=headers) as client:
        response = await client.get(url, params=params)
        response.raise_for_status()
        data = response.json()
    result = []
    for row in data or []:
        paper = row.get("paper") or row
        title = clean(paper.get("title") or row.get("title"))
        abstract = clean(paper.get("summary") or paper.get("abstract") or row.get("summary"))
        if not title or not is_research_tech(title, abstract):
            continue
        paper_id = str(paper.get("id") or row.get("id") or "")
        authors = []
        for author in paper.get("authors") or []:
            if isinstance(author, dict):
                authors.append(clean(author.get("name")))
            else:
                authors.append(clean(str(author)))
        published = parse_any_date(row.get("publishedAt") or paper.get("publishedAt"))
        landing = f"https://huggingface.co/papers/{paper_id}" if paper_id else "https://huggingface.co/papers"
        arxiv_id = paper.get("id") or ""
        pdf = f"https://arxiv.org/pdf/{arxiv_id}" if arxiv_id else ""
        result.append({
            "external_id": f"huggingface:{paper_id or _hash(title)}",
            "item_type": "paper",
            "title": title,
            "abstract": abstract,
            "authors": authors,
            "categories": topic_labels(title, abstract) + ["Hugging Face Papers"],
            "source": "Hugging Face Papers",
            "source_domain": "huggingface.co",
            "venue": "Hugging Face Papers",
            "paper_id": arxiv_id,
            "landing_url": landing,
            "pdf_url": pdf,
            "published_at": published,
            "updated_at": published,
            "citation_count": int(paper.get("citationCount") or 0),
            "doi": paper.get("doi") or "",
        })
    return result


async def fetch_semantic_scholar() -> list[dict]:
    query = "artificial intelligence machine learning large language models agents robotics"
    params = {
        "query": query,
        "limit": min(max(settings.semantic_scholar_limit, 1), 100),
        "fields": "title,abstract,authors,url,openAccessPdf,publicationDate,venue,citationCount,externalIds,fieldsOfStudy",
    }
    headers = {"User-Agent": "NewsRadar/3.0"}
    if settings.semantic_scholar_api_key:
        headers["x-api-key"] = settings.semantic_scholar_api_key
    async with httpx.AsyncClient(timeout=25.0, headers=headers) as client:
        response = await client.get("https://api.semanticscholar.org/graph/v1/paper/search", params=params)
        response.raise_for_status()
        data = response.json()
    result = []
    for row in data.get("data", []) or []:
        title = clean(row.get("title"))
        abstract = clean(row.get("abstract"))
        if not title or not is_research_tech(title, abstract):
            continue
        ext = row.get("externalIds") or {}
        arxiv_id = ext.get("ArXiv") or ""
        authors = [clean(a.get("name")) for a in row.get("authors") or [] if a.get("name")]
        landing = row.get("url") or (f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else "")
        pdf = (row.get("openAccessPdf") or {}).get("url") or (f"https://arxiv.org/pdf/{arxiv_id}" if arxiv_id else "")
        result.append({
            "external_id": f"s2:{ext.get('DOI') or arxiv_id or row.get('paperId')}",
            "item_type": "paper",
            "title": title,
            "abstract": abstract,
            "authors": authors,
            "categories": topic_labels(title, abstract) + ["Semantic Scholar"],
            "source": "Semantic Scholar",
            "source_domain": "semanticscholar.org",
            "venue": clean(row.get("venue")) or "",
            "paper_id": arxiv_id or row.get("paperId") or "",
            "landing_url": landing,
            "pdf_url": pdf,
            "published_at": parse_any_date(row.get("publicationDate")),
            "updated_at": None,
            "citation_count": int(row.get("citationCount") or 0),
            "doi": ext.get("DOI") or "",
        })
    return result


async def fetch_openalex() -> list[dict]:
    params = {
        "search": "artificial intelligence machine learning large language models robotics",
        "per-page": min(max(settings.openalex_limit, 1), 100),
        "sort": "publication_date:desc",
        "select": "id,doi,title,publication_date,authorships,primary_location,best_oa_location,open_access,cited_by_count,topics,concepts,abstract_inverted_index",
    }
    if settings.openalex_mailto:
        params["mailto"] = settings.openalex_mailto
    async with httpx.AsyncClient(timeout=30.0, headers={"User-Agent": "NewsRadar/3.0"}) as client:
        response = await client.get("https://api.openalex.org/works", params=params)
        response.raise_for_status()
        data = response.json()
    result = []
    for row in data.get("results", []) or []:
        title = clean(row.get("title"))
        inv = row.get("abstract_inverted_index") or {}
        abstract = " ".join(word for word, _ in sorted(((w, pos) for w, positions in inv.items() for pos in positions), key=lambda x: x[1])) if inv else ""
        if not title or not is_research_tech(title, abstract):
            continue
        authors = [clean((a.get("author") or {}).get("display_name")) for a in row.get("authorships") or []]
        loc = row.get("best_oa_location") or row.get("primary_location") or {}
        pdf = (loc.get("pdf_url") or "")
        landing = (loc.get("landing_page_url") or row.get("doi") or row.get("id") or "")
        topics = [clean((t.get("display_name") or "")) for t in row.get("topics") or []][:4]
        result.append({
            "external_id": f"openalex:{row.get('id') or _hash(title)}",
            "item_type": "paper",
            "title": title,
            "abstract": abstract,
            "authors": [a for a in authors if a],
            "categories": list(dict.fromkeys(topic_labels(title, abstract) + topics)),
            "source": "OpenAlex",
            "source_domain": urlparse(landing).netloc.lower().removeprefix("www."),
            "venue": clean(((loc.get("source") or {}).get("display_name") or "")),
            "paper_id": str(row.get("id") or ""),
            "landing_url": landing,
            "pdf_url": pdf,
            "published_at": parse_any_date(row.get("publication_date")),
            "updated_at": None,
            "citation_count": int(row.get("cited_by_count") or 0),
            "doi": str(row.get("doi") or ""),
        })
    return result


async def fetch_crossref() -> list[dict]:
    since = (datetime.now(timezone.utc) - timedelta(days=2)).strftime("%Y-%m-%d")
    params = {
        "query.bibliographic": "artificial intelligence machine learning large language models robotics",
        "filter": f"from-pub-date:{since}",
        "rows": min(max(settings.crossref_limit, 1), 100),
        "sort": "published",
        "order": "desc",
    }
    if settings.openalex_mailto:
        params["mailto"] = settings.openalex_mailto
    headers = {"User-Agent": f"NewsRadar/3.0 (mailto:{settings.openalex_mailto or 'personal@example.com'})"}
    async with httpx.AsyncClient(timeout=30.0, headers=headers) as client:
        response = await client.get("https://api.crossref.org/works", params=params)
        response.raise_for_status()
        data = response.json()
    result=[]
    for row in (data.get("message", {}).get("items") or []):
        title = clean((row.get("title") or [""])[0])
        abstract = clean(row.get("abstract"))
        if not title or not is_research_tech(title, abstract):
            continue
        authors=[]
        for a in row.get("author") or []:
            name = clean(" ".join(x for x in [a.get("given"), a.get("family")] if x))
            if name: authors.append(name)
        doi = row.get("DOI") or ""
        landing = row.get("URL") or (f"https://doi.org/{doi}" if doi else "")
        date_parts=((row.get("published") or {}).get("date-parts") or [])
        published=None
        if date_parts and date_parts[0]:
            parts=date_parts[0]
            try:
                published=datetime(parts[0], parts[1] if len(parts)>1 else 1, parts[2] if len(parts)>2 else 1, tzinfo=timezone.utc)
            except Exception:
                published=None
        result.append({
            "external_id": f"crossref:{doi or _hash(title)}", "item_type": "paper",
            "title": title, "abstract": abstract, "authors": authors,
            "categories": topic_labels(title, abstract) + ["Crossref"], "source": "Crossref",
            "source_domain": "doi.org" if doi else urlparse(landing).netloc.lower().removeprefix("www."),
            "venue": clean(((row.get("container-title") or [""])[0])), "paper_id": doi,
            "landing_url": landing, "pdf_url": "", "published_at": published, "updated_at": None,
            "citation_count": int(row.get("is-referenced-by-count") or 0), "doi": doi,
        })
    return result


async def fetch_research_blogs() -> list[dict]:
    import asyncio
    async def one(key: str, name: str, page_url: str, hints: tuple[str, ...]) -> list[dict]:
        try:
            async with httpx.AsyncClient(timeout=25.0, follow_redirects=True, headers={"User-Agent": "NewsRadar/3.0 (personal research reader)"}) as client:
                response = await client.get(page_url)
                response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            output, seen = [], set()
            for a in soup.select("article a[href], main a[href], a[href]"):
                href = a.get("href") or ""
                if not href:
                    continue
                url = __import__("urllib.parse", fromlist=["urljoin"]).urljoin(str(response.url), href)
                if not any(h in urlparse(url).path for h in hints) or url.rstrip("/") in {page_url.rstrip("/")}:
                    continue
                if url in seen:
                    continue
                title = clean(a.get_text(" ", strip=True) or a.get("aria-label") or a.get("title"))
                if len(title) < 12 or title.lower() in {"read more", "learn more", "view all"}:
                    continue
                box = a.find_parent(["article", "li", "section", "div"])
                summary = clean(box.find("p").get_text(" ", strip=True)) if box and box.find("p") else ""
                pub = None
                if box:
                    tm = box.find("time")
                    if tm:
                        pub = parse_any_date(tm.get("datetime") or tm.get_text(" ", strip=True))
                if not is_research_tech(title, summary):
                    continue
                output.append({
                    "external_id": f"research-blog:{key}:{_hash(url)}", "item_type": "news", "title": title,
                    "abstract": summary, "authors": [], "categories": topic_labels(title, summary) + ["Research News"],
                    "source": name, "source_domain": urlparse(url).netloc.lower().removeprefix("www."),
                    "venue": "", "paper_id": "", "landing_url": url, "pdf_url": "",
                    "published_at": pub, "updated_at": pub, "citation_count": 0, "doi": "",
                })
                seen.add(url)
                if len(output) >= 60:
                    break
            return output
        except Exception as exc:
            logger.warning("Research blog failed %s: %s", name, exc)
            return []
    results = await asyncio.gather(*(one(*x) for x in RESEARCH_BLOGS), return_exceptions=True)
    output = []
    for result in results:
        if not isinstance(result, Exception):
            output.extend(result)
    return output


async def fetch_research_news() -> list[dict]:
    async def one(query: str) -> list[dict]:
        params = {"q": f"({query}) {settings.research_google_news_window}", "hl": "en-IN", "gl": "IN", "ceid": "IN:en"}
        try:
            async with httpx.AsyncClient(timeout=20.0, headers={"User-Agent": "NewsRadar/3.0"}) as client:
                response = await client.get("https://news.google.com/rss/search", params=params)
                response.raise_for_status()
                parsed = feedparser.parse(response.content)
            out = []
            for entry in parsed.entries[:40]:
                title = clean(entry.get("title"))
                url = entry.get("link") or ""
                summary = clean(entry.get("summary") or entry.get("description"))
                if not title or not url or not is_research_tech(title, summary):
                    continue
                source_name = "Google News"
                source_domain = "news.google.com"
                src = entry.get("source")
                if src:
                    source_name = src.get("title") or source_name
                    source_link = src.get("href") or src.get("link") or ""
                    if source_link:
                        source_domain = urlparse(source_link).netloc.lower().removeprefix("www.") or source_domain
                pub = parse_any_date(entry.get("published") or entry.get("updated"))
                out.append({
                    "external_id": f"research-news:{_hash(title + url)}",
                    "item_type": "news",
                    "title": title,
                    "abstract": summary,
                    "authors": [],
                    "categories": topic_labels(title, summary) + ["Research News"],
                    "source": source_name,
                    "source_domain": source_domain,
                    "venue": "",
                    "paper_id": "",
                    "landing_url": url,
                    "pdf_url": "",
                    "published_at": pub,
                    "updated_at": pub,
                    "citation_count": 0,
                    "doi": "",
                })
            return out
        except Exception as exc:
            logger.warning("Research Google News query failed: %s", exc)
            return []
    batches = await __import__("asyncio").gather(*(one(q) for q in RESEARCH_NEWS_QUERIES))
    result: list[dict] = []
    for batch in batches:
        result.extend(batch)
    return result


async def collect_research() -> tuple[list[dict], dict]:
    import asyncio
    tasks = {
        "arxiv": fetch_arxiv(),
        "huggingface": fetch_huggingface_daily(),
        "semantic_scholar": fetch_semantic_scholar(),
        "openalex": fetch_openalex(),
        "crossref": fetch_crossref(),
        "research_blogs": fetch_research_blogs(),
        "research_news": fetch_research_news(),
    }
    results = await asyncio.gather(*tasks.values(), return_exceptions=True)
    all_items: list[dict] = []
    status: dict = {}
    for key, result in zip(tasks.keys(), results):
        if isinstance(result, Exception):
            status[key] = {"ok": False, "count": 0, "error": str(result)[:500]}
        else:
            status[key] = {"ok": True, "count": len(result)}
            all_items.extend(result)
    return all_items, status


def _coerce_dt(v):
    return v if isinstance(v, datetime) else parse_any_date(v)


def ingest_research(items: list[dict]) -> dict:
    created = 0
    interesting = 0
    paper_count = 0
    news_count = 0
    with session_scope() as db:
        interests = db.query(Interest).filter(Interest.enabled.is_(True)).all()
        # Use the same notification threshold as the article pipeline.
        from .news_pipeline import get_state
        threshold = float(get_state(db, "notification_threshold", str(settings.notification_threshold)) or settings.notification_threshold)

        from sqlalchemy import select
        existing_ext = set(db.scalars(select(ResearchItem.external_id)).all())
        existing_doi = {x for x in db.scalars(select(ResearchItem.doi)).all() if x}
        existing_paper_id = {x for x in db.scalars(select(ResearchItem.paper_id)).all() if x}

        for raw in items:
            title = (raw.get("title") or "").strip()
            if not title:
                continue
            ext = str(raw.get("external_id") or f"paper:{_hash(title)}")[:300]
            doi = str(raw.get("doi") or "")[:500]
            paper_id = str(raw.get("paper_id") or "")[:500]
            if ext in existing_ext or (doi and doi in existing_doi) or (paper_id and paper_id in existing_paper_id):
                continue
            existing_ext.add(ext)
            if doi:
                existing_doi.add(doi)
            if paper_id:
                existing_paper_id.add(paper_id)

            abstract = raw.get("abstract") or ""
            if not is_research_tech(title, abstract, raw.get("categories") or []):
                continue
            score, matches = score_paper(title, abstract, interests)
            item = ResearchItem(
                external_id=ext,
                item_type=str(raw.get("item_type", "paper"))[:50],
                title=title[:2000],
                abstract=abstract,
                authors=json.dumps(raw.get("authors") or []),
                categories=json.dumps(list(dict.fromkeys(raw.get("categories") or ["Research"]))),
                source=str(raw.get("source", "") or "")[:500],
                source_domain=str(raw.get("source_domain", "") or "")[:500],
                venue=str(raw.get("venue", "") or "")[:500],
                doi=doi,
                paper_id=paper_id,
                landing_url=str(raw.get("landing_url", "") or "")[:2000],
                pdf_url=str(raw.get("pdf_url", "") or "")[:2000],
                published_at=_coerce_dt(raw.get("published_at")),
                updated_at=_coerce_dt(raw.get("updated_at")),
                citation_count=int(raw.get("citation_count") or 0),
                relevance_score=score,
                matched_keywords=json.dumps(matches),
            )
            db.add(item)
            created += 1
            if item.item_type == "paper":
                paper_count += 1
            else:
                news_count += 1
            if score >= threshold:
                db.add(ResearchNotification(research_item=item, status="pending"))
                interesting += 1
    return {"fetched": len(items), "created": created, "papers_created": paper_count, "news_created": news_count, "interesting": interesting}
