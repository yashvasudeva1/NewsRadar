from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "NewsRadar"
    environment: str = "development"
    secret_key: str = "change-me"
    port: int = 8000
    database_url: str = "sqlite:///./data/news.db"
    allowed_hosts: str = "*"

    # News aggregation
    currents_api_key: str = ""
    currents_base_url: str = "https://api.currentsapi.services/v1"
    news_language: str = "en"
    news_country: str = ""
    enable_gdelt: bool = True
    enable_google_news_rss: bool = True
    enable_hacker_news: bool = True
    newsapi_api_key: str = ""
    gnews_api_key: str = ""
    gdelt_timespan: str = "30min"
    gdelt_maxrecords: int = 250
    google_news_window: str = "when:30m"
    official_poll_minutes: int = 5
    aggregator_poll_minutes: int = 10
    currents_poll_minutes: int = 30
    source_concurrency: int = 12
    max_items_per_source: int = 80
    news_category: str = "technology"
    latest_pages: int = 2
    latest_page_size: int = 20

    # Research / papers
    enable_research_collectors: bool = True
    research_poll_minutes: int = 15
    arxiv_max_results: int = 100
    huggingface_papers_limit: int = 100
    huggingface_token: str = ""
    semantic_scholar_api_key: str = ""
    semantic_scholar_limit: int = 50
    openalex_mailto: str = ""
    openalex_limit: int = 100
    crossref_limit: int = 100
    research_google_news_window: str = "when:2h"

    # Gmail OAuth
    google_client_secrets_file: str = "credentials.json"
    # For Vercel, prefer the JSON string stored in an environment variable.
    # Local development can continue to use credentials.json.
    google_client_secret_json: str = ""
    google_redirect_uri: str = "http://localhost:8000/api/auth/gmail/callback"
    gmail_scope: str = "https://www.googleapis.com/auth/gmail.send"
    notification_recipient: str = ""

    # Scheduler / cron
    # Vercel automatically sends CRON_SECRET as a Bearer token for Cron Jobs.
    # CRON_TOKEN remains as a local/CI compatibility fallback.
    cron_secret: str = ""
    cron_token: str = "change-this-cron-token"
    enable_scheduler: bool = True
    initial_fetch_on_startup: bool = True

    # Notifications
    notification_threshold: float = 5.0
    notification_mode: str = "morning_top5"
    morning_digest_limit: int = 5
    morning_digest_hours: int = 24
    morning_digest_min_score: float = 1.0
    app_base_url: str = ""

    # Broad technical vocabulary. Official sources are accepted as tech by source
    # identity; this list primarily filters Google News/GDELT/HN discovery.
    tech_terms: str = (
        "ai,artificial intelligence,machine learning,deep learning,llm,large language model,"
        "foundation model,agentic ai,ai agent,openai,chatgpt,gpt,anthropic,claude,gemini,gemma,"
        "google ai,deepmind,copilot,meta ai,nvidia,cuda,tensorrt,amd,intel,qualcomm,apple silicon,"
        "semiconductor,chip,chipset,gpu,cpu,tpu,data center,datacenter,cloud,kubernetes,docker,"
        "serverless,api,sdk,developer,programming,software,open source,linux,github,gitlab,python,"
        "pytorch,tensorflow,keras,hugging face,transformers,vllm,triton,robotics,robot,computer vision,"
        "cybersecurity,security vulnerability,zero trust,web,chrome,android,ios,iphone,mac,wearable,"
        "ar,vr,xr,quantum,edge ai,mlops,vector database,database,startup,venture,cloudflare,aws,"
        "azure,google cloud,microsoft,oracle,ibm,databricks,supabase,vercel,mistral,cohere,xai"
    )

    default_keywords: str = (
        "OpenAI,Google AI,Google DeepMind,Anthropic,NVIDIA,Microsoft,Meta AI,Apple,Amazon AWS,GitHub,"
        "Mistral,Cohere,xAI,AI,LLM,AI agents,Gemini,Claude,GPT,CUDA,PyTorch,developers,open source,"
        "cybersecurity,cloud,kubernetes,robotics,semiconductors,startups,databases"
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    import os

    s = Settings()
    if not os.getenv("DATABASE_URL"):
        # Neon / Vercel Postgres integrations may expose different names.
        for name in ("POSTGRES_URL", "DATABASE_URL_UNPOOLED", "POSTGRES_PRISMA_URL", "NEON_DATABASE_URL"):
            value = os.getenv(name)
            if value:
                s.database_url = value
                break
    return s


settings = get_settings()
