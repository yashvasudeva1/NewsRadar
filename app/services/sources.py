from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlparse


@dataclass(frozen=True)
class SourceDefinition:
    key: str
    name: str
    kind: Literal["official", "rss", "aggregator", "community"]
    url: str
    mode: Literal["rss", "html", "auto"]
    article_path_hints: tuple[str, ...] = ()
    headers: tuple[tuple[str, str], ...] = (
        ("User-Agent", "NewsRadar/4.0 (+personal technology monitor)"),
        ("Accept", "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"),
    )

    @property
    def domain(self) -> str:
        return urlparse(self.url).netloc.lower().removeprefix("www.")


# First-party sources are deliberately preferred over aggregators.  HTML is used
# where the source exposes a public newsroom/blog but no stable RSS endpoint.
# RSS is used where a public feed is available because it is cheaper and less
# brittle than scraping a whole page.
OFFICIAL_SOURCES: tuple[SourceDefinition, ...] = (
    # Frontier AI / model labs
    SourceDefinition("openai", "OpenAI", "official", "https://openai.com/news/", "auto", ("/index/",)),
    SourceDefinition("anthropic", "Anthropic", "official", "https://www.anthropic.com/news", "auto", ("/news/",)),
    SourceDefinition("google-ai", "Google AI", "official", "https://blog.google/innovation-and-ai/technology/ai/", "auto", ("/innovation-and-ai/",)),
    SourceDefinition("google-deepmind", "Google DeepMind", "official", "https://deepmind.google/blog/", "auto", ("/blog/",)),
    SourceDefinition("google-research", "Google Research", "official", "https://research.google/blog/", "auto", ("/blog/",)),
    SourceDefinition("google-dev", "Google Developers", "official", "https://developers.googleblog.com/en/", "auto", ("/en/",)),
    SourceDefinition("microsoft", "Microsoft", "official", "https://blogs.microsoft.com/", "auto", ("/blog/",)),
    SourceDefinition("microsoft-dev", "Microsoft Developer Blogs", "official", "https://devblogs.microsoft.com/", "auto", ("/",)),
    SourceDefinition("microsoft-research", "Microsoft Research", "official", "https://www.microsoft.com/en-us/research/blog/", "auto", ("/en-us/research/blog/",)),
    SourceDefinition("meta-ai", "Meta AI", "official", "https://ai.meta.com/blog/", "auto", ("/blog/",)),
    SourceDefinition("mistral", "Mistral AI", "official", "https://mistral.ai/news/", "auto", ("/news/",)),
    SourceDefinition("cohere", "Cohere", "official", "https://cohere.com/blog", "auto", ("/blog/",)),
    SourceDefinition("xai", "xAI", "official", "https://x.ai/news", "auto", ("/news",)),

    # Compute / cloud / developer platforms
    SourceDefinition("nvidia", "NVIDIA Developer", "official", "https://developer.nvidia.com/blog/", "auto", ("/blog/",)),
    SourceDefinition("aws", "AWS What's New", "official", "https://aws.amazon.com/about-aws/whats-new/recent/feed/", "rss"),
    SourceDefinition("aws-ml", "AWS Machine Learning", "official", "https://aws.amazon.com/blogs/machine-learning/category/artificial-intelligence/amazon-machine-learning/", "auto", ("/blogs/machine-learning/",)),
    SourceDefinition("azure", "Microsoft Azure Blog", "official", "https://azure.microsoft.com/en-us/blog/", "auto", ("/en-us/blog/",)),
    SourceDefinition("github", "GitHub Changelog", "official", "https://github.blog/changelog/", "auto", ("/changelog/",)),
    SourceDefinition("github-blog", "GitHub Blog", "official", "https://github.blog/", "auto", ("/",)),
    SourceDefinition("cloudflare", "Cloudflare", "official", "https://blog.cloudflare.com/rss/", "rss"),
    SourceDefinition("supabase", "Supabase", "official", "https://supabase.com/blog", "auto", ("/blog/",)),
    SourceDefinition("vercel", "Vercel", "official", "https://vercel.com/changelog", "auto", ("/changelog/",)),
    SourceDefinition("docker", "Docker", "official", "https://www.docker.com/blog/", "auto", ("/blog/",)),
    SourceDefinition("kubernetes", "Kubernetes", "official", "https://kubernetes.io/blog/", "auto", ("/blog/",)),
    SourceDefinition("pytorch", "PyTorch", "official", "https://pytorch.org/blog/?format=rss", "rss"),
    SourceDefinition("huggingface", "Hugging Face", "official", "https://huggingface.co/blog", "auto", ("/blog/",)),
    SourceDefinition("jetbrains", "JetBrains", "official", "https://blog.jetbrains.com/", "auto", ("/",)),
    SourceDefinition("mozilla", "Mozilla", "official", "https://blog.mozilla.org/", "auto", ("/blog/",)),
    SourceDefinition("chromium", "Chromium", "official", "https://blog.chromium.org/", "auto", ("/20",)),

    # Hardware / devices
    SourceDefinition("apple", "Apple Newsroom", "official", "https://www.apple.com/newsroom/", "auto", ("/newsroom/",)),
    SourceDefinition("apple-ml", "Apple Machine Learning Research", "official", "https://machinelearning.apple.com/", "auto", ("/research/",)),
    SourceDefinition("amd", "AMD", "official", "https://www.amd.com/en/newsroom/press-releases.html", "auto", ("/en/newsroom/",)),
    SourceDefinition("intel", "Intel", "official", "https://www.intel.com/content/www/us/en/newsroom/home.html", "auto", ("/content/www/us/en/newsroom/",)),
    SourceDefinition("qualcomm", "Qualcomm", "official", "https://www.qualcomm.com/news/releases", "auto", ("/news/releases/",)),
    SourceDefinition("samsung", "Samsung Newsroom", "official", "https://news.samsung.com/global/latest", "auto", ("/global/",)),
)


# Broad discovery is a supplement, not the canonical source of official announcements.
AGGREGATOR_QUERIES = (
    "AI OR \"artificial intelligence\" OR LLM OR \"machine learning\" OR \"AI agents\" OR OpenAI OR Anthropic OR Gemini OR NVIDIA OR Microsoft OR Meta AI OR Apple AI OR AWS OR GitHub OR cybersecurity OR semiconductor OR GPU OR cloud OR Kubernetes OR developer OR open source OR robotics",
)

GOOGLE_NEWS_QUERIES = (
    "OpenAI OR Anthropic OR Gemini OR NVIDIA OR \"Meta AI\" OR \"Microsoft AI\" OR \"Apple AI\" OR Mistral OR Cohere OR xAI",
    "AI OR LLM OR \"AI agents\" OR \"machine learning\" OR robotics OR cybersecurity",
    "NVIDIA OR AMD OR Intel OR Qualcomm OR semiconductor OR GPU OR TPU",
    "AWS OR Azure OR \"Google Cloud\" OR Cloudflare OR Kubernetes OR Docker OR Vercel OR Supabase",
    "GitHub OR developer OR SDK OR API OR \"open source\" OR PyTorch OR TensorFlow OR Hugging Face",
)

SOURCE_KIND_NAMES = {
    "official": "Official",
    "rss": "RSS",
    "aggregator": "News index",
    "community": "Community",
}

OFFICIAL_DOMAINS = {source.domain for source in OFFICIAL_SOURCES}
