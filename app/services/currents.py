from datetime import datetime, timezone
from urllib.parse import urlparse
import httpx
from ..config import settings


class CurrentsError(RuntimeError):
    pass


class CurrentsClient:
    def __init__(self) -> None:
        self.base_url = settings.currents_base_url.rstrip("/")
        self.headers = {
            "Authorization": f"Bearer {settings.currents_api_key}",
            "Accept": "application/json",
            "User-Agent": "NewsRadar/1.0",
        }

    async def latest_news(self) -> list[dict]:
        """Fetch several pages of latest technology news.

        The free plan is request-limited, so the default of 4 pages x 20
        articles keeps the scheduled 30-minute fetch comfortably below the
        current daily request ceiling while providing a much larger local
        article pool.
        """
        if not settings.currents_api_key:
            raise CurrentsError("CURRENTS_API_KEY is not configured")

        pages = max(1, min(settings.latest_pages, 10))
        page_size = max(1, min(settings.latest_page_size, 20))
        articles: list[dict] = []

        async with httpx.AsyncClient(timeout=20.0) as client:
            for page in range(1, pages + 1):
                params = {
                    "language": settings.news_language,
                    "category": settings.news_category,
                    "page_size": page_size,
                    "page_number": page,
                }
                if settings.news_country:
                    params["country"] = settings.news_country.upper()

                response = await client.get(
                    f"{self.base_url}/latest-news",
                    params=params,
                    headers=self.headers,
                )

                if response.status_code != 200:
                    raise CurrentsError(
                        f"Currents API {response.status_code}: {response.text[:500]}"
                    )

                payload = response.json()
                if payload.get("status") != "ok":
                    raise CurrentsError(str(payload))

                batch = payload.get("news", []) or []
                articles.extend(batch)

                # Avoid making needless calls when a page contains no data.
                if len(batch) < page_size:
                    break

        # Deduplicate while preserving source order.
        seen: set[str] = set()
        result: list[dict] = []
        for article in articles:
            key = article.get("id") or article.get("url") or article.get("title")
            if not key or key in seen:
                continue
            seen.add(key)
            result.append(article)

        return result

    async def health(self) -> dict:
        if not settings.currents_api_key:
            return {"configured": False, "reachable": False}

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"{self.base_url}/auth",
                headers=self.headers,
            )
        return {
            "configured": True,
            "reachable": response.status_code == 200,
            "status_code": response.status_code,
        }


def parse_published(value: str | None) -> datetime | None:
    if not value:
        return None
    value = value.replace("+0000", "+00:00")
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def source_domain(url: str) -> str:
    try:
        return urlparse(url).netloc.lower().removeprefix("www.")
    except Exception:
        return ""
