<p align="center">
  <img src="app/static/logo.svg" alt="NewsRadar Logo" width="120" height="120" />
</p>

# NewsRadar

NewsRadar is an automated technology intelligence radar and scholarly research monitoring system. It aggregates first-party engineering blogs, global tech news, and preprint academic papers into a unified, relevance-scored stream. The application features an integrated distraction-free reader and delivers a personalized top-five morning briefing directly to Gmail.

---

## Table of Contents

- [Overview](#overview)
- [System Architecture](#system-architecture)
- [Core Features](#core-features)
- [Data Ingestion Sources](#data-ingestion-sources)
  - [First-Party Engineering Blogs](#first-party-engineering-blogs)
  - [Scholarly Research Repositories](#scholarly-research-repositories)
  - [Aggregators and News Indexes](#aggregators-and-news-indexes)
- [Repository Structure](#repository-structure)
- [Configuration Reference](#configuration-reference)
- [Local Development Setup](#local-development-setup)
- [Production Deployment](#production-deployment)
  - [1. Database Configuration (Neon PostgreSQL)](#1-database-configuration-neon-postgresql)
  - [2. Google OAuth and Gmail Integration](#2-google-oauth-and-gmail-integration)
  - [3. Vercel Deployment](#3-vercel-deployment)
  - [4. Scheduled Ingestion Orchestration](#4-scheduled-ingestion-orchestration)
- [API Reference](#api-reference)
- [Security and Authentication](#security-and-authentication)
- [License](#license)

---

## Overview

Staying informed across the fast-moving artificial intelligence, systems, and developer tooling landscapes requires tracking disparate sources: corporate engineering logs, academic preprint servers, and general technology news feeds. 

NewsRadar solves this fragmentation by:
1. Polling official corporate feeds and academic repositories concurrently.
2. Filtering and scoring articles using weighted user keywords.
3. Normalizing and storing metadata in a relational database.
4. Providing a clean, responsive web interface with an integrated readability extractor.
5. Delivering an automated, deduplicated morning digest of the five highest-scoring articles to the user's Gmail inbox.

---

## System Architecture

The system operates across four primary stages:

```
[ Data Ingestion ]
  +-- First-party RSS & Atom Feeds (OpenAI, Anthropic, Google, Apple, etc.)
  +-- Academic APIs (arXiv, Hugging Face, Semantic Scholar, OpenAlex, Crossref)
  +-- Aggregator Feeds (Hacker News, GDELT, Google News RSS, Currents)
          |
          v
[ Pipeline & Scoring ]
  +-- Concurrency-controlled async fetchers (HTTPX)
  +-- Deduplication (SHA-1 URL/Title hash & Title fingerprinting)
  +-- Topic classification & keyword-weighted relevance scoring
          |
          v
[ Storage & Persistence ]
  +-- PostgreSQL (Neon serverless pooled) / SQLite (Local development)
  +-- SQLAlchemy 2.0 ORM with schema migrations
          |
          v
[ Consumption & Delivery ]
  +-- Mobile-responsive web UI (FastAPI + Jinja2 + CSS)
  +-- Clean text extractor (Trafilatura)
  +-- Daily 09:00 IST Morning Top-5 Briefing (Google Gmail API)
```

---

## Core Features

- **Multi-Source Aggregation**: Concurrently queries first-party engineering portals, academic repositories, and broad news indexes.
- **Weighted Relevance Scoring**: Articles receive a numerical score based on configurable keyword matches, prioritizing high-signal developments over generic reporting.
- **Distraction-Free Reader**: Employs server-side text extraction to render article bodies without advertisements, pop-ups, or layout shifts.
- **Academic Research Integration**: Dedicated tracking for academic papers with abstract inspection, author listing, citation count tracking, and direct links to arXiv/PDF landing pages.
- **Automated Morning Digest**: Sends a curated email digest of the top five highest-scoring articles from the preceding 24 hours at 09:00 IST (03:30 UTC).
- **Serverless-Optimized Architecture**: Designed to run statelessly on Vercel Functions with Neon PostgreSQL pooling, avoiding long-lived server costs.
- **Fault-Tolerant Pipeline**: Network timeouts, upstream rate limits (e.g., HTTP 403/429), or malformed markup on individual feeds fail gracefully without interrupting the overall collection cycle.

---

## Data Ingestion Sources

### First-Party Engineering Blogs
NewsRadar monitors official announcements, research blogs, and changelogs directly from industry leaders:
- **Artificial Intelligence Labs**: OpenAI, Anthropic, Google DeepMind, Google Research, Meta AI, Mistral AI, Cohere, xAI.
- **Cloud and Enterprise Platforms**: Microsoft Azure, AWS What's New, AWS Machine Learning, Cloudflare, Supabase, Vercel.
- **Hardware and Semiconductor Vendors**: NVIDIA Developer, Intel Newsroom, AMD Newsroom, Qualcomm, Samsung Newsroom.
- **Developer Tooling and Systems**: GitHub Blog & Changelog, Docker, Kubernetes, PyTorch, Hugging Face, JetBrains, Mozilla, Chromium, Apple ML Research.

### Scholarly Research Repositories
- **arXiv**: Daily CS, AI, and Machine Learning paper preprints.
- **Hugging Face Daily Papers**: Trending community-curated machine learning publications.
- **Semantic Scholar**: Query-driven exploration of academic literature with citation indexing.
- **OpenAlex & Crossref**: Open metadata exploration across global scholarly journals and publications.

### Aggregators and News Indexes
- **Hacker News**: Top trending submissions and engineering discussions via Firebase API.
- **Google News RSS**: Precision temporal queries targeting major technology companies and breakthrough topics.
- **GDELT Project**: Global automated event and news tracking.
- **Currents API**: Optional commercial international news index.

---

## Repository Structure

```
news_radar/
├── .github/
│   └── workflows/
│       └── news-ingest.yml       # Scheduled background ingestion workflow
├── app/
│   ├── api.py                    # REST API endpoints and cron webhooks
│   ├── config.py                 # Application configuration and settings schema
│   ├── db.py                     # SQLAlchemy database engine and connection pool
│   ├── main.py                   # FastAPI initialization, routing, and lifecycle
│   ├── models.py                 # Database models (Articles, Research, Interests, etc.)
│   ├── schemas.py                # Pydantic request and response schemas
│   ├── services/
│   │   ├── currents.py           # Currents API client
│   │   ├── filtering.py          # Topic classification and relevance scoring
│   │   ├── gmail.py              # Google OAuth 2.0 and Gmail API integration
│   │   ├── multi_source.py       # Concurrent RSS/HTML collectors for corporate blogs
│   │   ├── news_pipeline.py      # Ingestion pipeline, deduplication, and persistence
│   │   ├── reader.py             # Article extraction service using Trafilatura
│   │   ├── research.py           # Academic collectors (arXiv, HF, OpenAlex, etc.)
│   │   └── sources.py            # Registry of feed endpoints and search queries
│   ├── static/                   # Static stylesheets and assets
│   └── templates/                # Jinja2 HTML templates for the frontend
├── .env.example                  # Environment configuration template
├── main.py                       # Root Vercel entrypoint
├── requirements.txt              # Production Python dependencies
└── vercel.json                   # Vercel deployment and cron configuration
```

---

## Configuration Reference

The application reads configuration through environment variables using Pydantic Settings.

| Variable | Required | Default | Description |
|---|---|---|---|
| `DATABASE_URL` | Yes (in prod) | `sqlite:///./data/news.db` | PostgreSQL connection string (`postgresql+psycopg://...`) or SQLite URL. |
| `SECRET_KEY` | Yes | - | Secret key for session encryption and signing. |
| `APP_BASE_URL` | Yes (in prod) | `http://localhost:8000` | Fully-qualified public URL of the application. |
| `CRON_SECRET` | Recommended | - | Shared secret for authenticating scheduled Vercel and CI ingestion requests. |
| `GOOGLE_CLIENT_SECRET_JSON` | Optional | - | Full Google OAuth client JSON string (single-line) for Vercel deployment. |
| `GOOGLE_REDIRECT_URI` | Optional | `http://localhost:8000/api/auth/gmail/callback` | OAuth redirect URI registered in Google Cloud Console. |
| `NOTIFICATION_RECIPIENT` | Optional | - | Destination email address for the daily morning briefing. |
| `NOTIFICATION_THRESHOLD` | No | `5.0` | Minimum score required for an article to be marked as interesting. |
| `MORNING_DIGEST_LIMIT` | No | `5` | Maximum number of articles selected for the daily morning digest. |
| `ENABLE_SCHEDULER` | No | `true` (local) / `false` (Vercel) | Enables the in-process background polling scheduler for local development. |
| `ENABLE_GDELT` | No | `true` | Enables polling the GDELT Project API. |
| `ENABLE_GOOGLE_NEWS_RSS` | No | `true` | Enables polling Google News RSS queries. |
| `ENABLE_HACKER_NEWS` | No | `true` | Enables polling the Hacker News API. |
| `ENABLE_RESEARCH_COLLECTORS` | No | `true` | Enables polling arXiv, Hugging Face, OpenAlex, and Crossref. |
| `SEMANTIC_SCHOLAR_API_KEY` | No | - | Optional API key to increase Semantic Scholar rate limits. |
| `HUGGINGFACE_TOKEN` | No | - | Optional Hugging Face User Access Token. |
| `CURRENTS_API_KEY` | No | - | Optional Currents API key for secondary news aggregation. |

---

## Local Development Setup

### Prerequisites
- Python 3.10, 3.11, or 3.12
- Git

### Installation Steps

1. **Clone the repository:**
   ```bash
   git clone https://github.com/yashvasudeva1/NewsRadar.git
   cd NewsRadar
   ```

2. **Create and activate a virtual environment:**
   - **Linux / macOS:**
     ```bash
     python3 -m venv .venv
     source .venv/bin/activate
     ```
   - **Windows (PowerShell):**
     ```powershell
     python -m venv .venv
     .venv\Scripts\Activate.ps1
     ```

3. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

4. **Initialize configuration:**
   ```bash
   cp .env.example .env
   ```
   Edit `.env` to configure your preferred settings, secrets, and keyword parameters.

5. **Start the development server:**
   ```bash
   uvicorn app.main:app --reload --port 8000
   ```

6. **Access the application:**
   Open `http://localhost:8000` in your browser. The SQLite database will be automatically created under `./data/news.db`.

---

## Production Deployment

NewsRadar is engineered to run seamlessly on Vercel's serverless Python runtime paired with a serverless PostgreSQL database (such as Neon).

### 1. Database Configuration (Neon PostgreSQL)

1. Provision a PostgreSQL instance via [Neon](https://neon.tech) or through the Vercel Marketplace Storage tab.
2. Obtain the pooled connection string (e.g., `postgresql://user:password@ep-xyz-pooler.region.neon.tech/neondb?sslmode=require`).
3. Set this value as `DATABASE_URL` in your Vercel project environment variables. The application automatically handles connection pooling and psycopg driver compatibility.

### 2. Google OAuth and Gmail Integration

To enable automated morning email briefings:
1. Navigate to the [Google Cloud Console](https://console.cloud.google.com/) and create a project.
2. Enable the **Gmail API** in APIs & Services.
3. Configure the **OAuth Consent Screen** (User Type: External, status: Testing, add your email address as a Test User).
4. Create **OAuth Client ID** credentials:
   - Application Type: Web application.
   - Authorized redirect URIs: `https://YOUR-APP-NAME.vercel.app/api/auth/gmail/callback`
5. Download the credentials JSON and set the `GOOGLE_CLIENT_SECRET_JSON` environment variable in Vercel with its full JSON contents in a single line.
6. Set `GOOGLE_REDIRECT_URI` to `https://YOUR-APP-NAME.vercel.app/api/auth/gmail/callback`.

### 3. Vercel Deployment

1. Install the Vercel CLI:
   ```bash
   npm install -g vercel
   ```
2. Link and deploy the application:
   ```bash
   vercel link
   vercel --prod
   ```
3. Alternatively, import the repository directly via the [Vercel Dashboard](https://vercel.com/new).

### 4. Scheduled Ingestion Orchestration

Because serverless functions terminate immediately after processing an HTTP request, ingestion must be triggered via scheduled HTTP webhooks.

NewsRadar supports a hybrid orchestration approach:

- **Daily Morning Digest via Vercel Cron**:
  Configured in `vercel.json` to execute at `03:30 UTC` (09:00 IST):
  ```json
  {
    "path": "/api/cron/morning-digest",
    "schedule": "30 3 * * *"
  }
  ```

- **Continuous Ingestion via GitHub Actions**:
  To keep the database updated throughout the day without incurring Vercel Pro subscription costs, the repository includes `.github/workflows/news-ingest.yml`. This workflow triggers the ingestion endpoints every 10 minutes.

  Configure the following GitHub repository secrets under **Settings → Secrets and variables → Actions**:
  - `VERCEL_APP_URL`: The production URL of your deployment (e.g., `https://news-radar-ten.vercel.app`).
  - `CRON_SECRET`: The authentication secret matching `CRON_SECRET` configured in Vercel.

---

## API Reference

All endpoints return JSON responses unless otherwise noted.

### Public Endpoints
- `GET /` — Responsive web application UI.
- `GET /api/health` — System status, database connectivity, and timestamp.
- `GET /api/news` — Paginated and filtered list of news articles.
  - Parameters: `q` (search query), `topic`, `source`, `language`, `official` (boolean), `limit`, `offset`.
- `GET /api/research/items` — Paginated and filtered list of research papers and academic news.
  - Parameters: `q`, `item_type` (`all`, `paper`, `news`), `source`, `limit`, `offset`.
- `GET /reader?url=<encoded_url>` — Reader view providing clutter-free article extraction.

### Management & Configuration
- `GET /api/interests` — Lists all registered interest keywords and weights.
- `POST /api/interests` — Adds or updates an interest keyword.
- `PATCH /api/interests/{id}` — Toggles or adjusts the weight of a specific keyword.
- `DELETE /api/interests/{id}` — Removes an interest keyword.

### Cron & Ingestion Endpoints
These endpoints require an `Authorization: Bearer <CRON_SECRET>` or `X-Cron-Token: <CRON_SECRET>` header:
- `GET /api/cron/ingest/official` — Ingests official first-party engineering sources.
- `GET /api/cron/ingest/aggregators` — Ingests aggregator feeds (Hacker News, GDELT, Google News).
- `GET /api/cron/ingest/research` — Ingests academic preprints and research papers.
- `GET /api/cron/morning-digest` — Evaluates top articles and dispatches the daily Gmail briefing.

---

## Security and Authentication

- **Cron Token Verification**: All automated ingestion endpoints enforce constant-time string comparison (`secrets.compare_digest`) against configured cron secrets to protect against timing attacks.
- **Minimal OAuth Scope**: Gmail authorization requires only `https://www.googleapis.com/auth/gmail.send`, preventing access to read, modify, or delete existing user mailbox messages.
- **Database Safety**: SQL statements use parameterized queries via SQLAlchemy 2.0 to guard against injection. Incoming text lengths are defensively validated and column types are normalized to prevent truncation faults.

---

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for complete details.
