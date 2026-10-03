from __future__ import annotations

import json
import re
from dataclasses import dataclass

from ..models import Article, Interest


TOPIC_RULES = {
    "AI": ["ai", "artificial intelligence", "llm", "language model", "foundation model", "agentic", "agent", "gemini", "claude", "gpt", "copilot", "deepmind", "openai"],
    "Developers": ["developer", "programming", "sdk", "api", "github", "gitlab", "ide", "compiler", "code", "devtools"],
    "Cloud": ["cloud", "aws", "azure", "google cloud", "kubernetes", "docker", "serverless", "data center", "datacenter"],
    "Cybersecurity": ["security", "cyber", "vulnerability", "zero trust", "ransomware", "malware", "cve"],
    "Chips": ["gpu", "cpu", "tpu", "npu", "semiconductor", "chip", "silicon", "cuda", "tensor rt", "foundry"],
    "Robotics": ["robot", "robotics", "humanoid", "physical ai", "autonomous"],
    "Open Source": ["open source", "open-source", "oss", "linux", "apache", "kubernetes", "pytorch", "tensorflow", "hugging face"],
    "Startups": ["startup", "funding", "venture", "series a", "series b", "seed round", "acquisition", "ipo"],
    "Hardware": ["iphone", "mac", "android", "smartphone", "laptop", "wearable", "headset", "display", "device", "tablet"],
    "Web": ["browser", "chrome", "web", "javascript", "typescript", "html", "css"],
    "Data": ["database", "sql", "vector database", "analytics", "data engineering", "data platform"],
    "Quantum": ["quantum", "qubit"],
}


@dataclass
class RelevanceResult:
    score: float
    matched_keywords: list[str]


def normalize(value: str) -> str:
    value = value.lower()
    value = re.sub(r"[^\w\s.-]", " ", value, flags=re.UNICODE)
    return re.sub(r"\s+", " ", value).strip()


def classify_topics(title: str, description: str) -> list[str]:
    text = normalize(f"{title} {description}")
    topics = ["Technology"]
    for topic, terms in TOPIC_RULES.items():
        if any(term in text for term in terms):
            topics.append(topic)
    return topics


def is_technology(title: str, description: str, categories: list[str] | None = None, source_kind: str = "aggregator", source_key: str = "") -> bool:
    existing = {normalize(x) for x in (categories or [])}
    if source_key in {
        "openai", "anthropic", "google-ai", "google-dev", "microsoft", "microsoft-dev",
        "meta-ai", "nvidia", "aws", "github", "cloudflare", "pytorch", "kubernetes",
        "docker", "huggingface", "amd", "intel", "qualcomm"
    }:
        return True
    if "technology" in existing and any(x in existing for x in {"ai", "developers", "cloud", "cybersecurity", "chips", "robotics", "open source", "hardware", "web", "data", "quantum"}):
        return True
    if source_kind == "official":
        topics = classify_topics(title, description)
        return len(topics) > 1
    text = normalize(f"{title} {description}")
    tech_terms = [
        "ai", "artificial intelligence", "llm", "machine learning", "openai", "anthropic", "gemini", "nvidia",
        "microsoft", "meta ai", "apple", "aws", "azure", "cloud", "kubernetes", "docker", "github", "developer",
        "programming", "software", "semiconductor", "gpu", "cpu", "robotics", "cybersecurity", "open source", "pytorch",
        "tensorflow", "quantum", "android", "iphone", "mac", "browser", "web", "database", "startup"
    ]
    return any(term in text for term in tech_terms)


def score_article(article: dict, interests: list[Interest]) -> RelevanceResult:
    title = normalize(article.get("title") or "")
    description = normalize(article.get("description") or "")

    score = 0.0
    matches: list[str] = []

    for interest in interests:
        if not interest.enabled:
            continue
        keyword = normalize(interest.keyword)
        if not keyword:
            continue
        if keyword in title:
            score += 5.0 * interest.weight
            matches.append(interest.keyword)
        elif keyword in description:
            score += 2.0 * interest.weight
            matches.append(interest.keyword)

    # Official release signals are useful even before a user adds a keyword.
    if article.get("source_kind") == "official":
        score += 1.5

    return RelevanceResult(round(score, 2), sorted(set(matches), key=str.lower))


def categories_json(categories) -> str:
    return json.dumps(categories if isinstance(categories, list) else [categories] if categories else [])


def article_to_dict(article: Article) -> dict:
    return {
        "id": article.id,
        "title": article.title,
        "description": article.description,
        "url": article.url,
        "author": article.author,
        "image_url": article.image_url,
        "categories": json.loads(article.categories or "[]"),
        "published_at": article.published_at,
        "source_domain": article.source_domain,
        "relevance_score": article.relevance_score,
        "matched_keywords": json.loads(article.matched_keywords or "[]"),
        "is_saved": article.is_saved,
    }
