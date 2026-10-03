# NewsRadar

NewsRadar is a lightweight, mobile-first personal technology and research monitor. It combines official company blogs/changelogs, broad news discovery and research indexes into one feed. Articles can be read inside the app when public page extraction is available. Gmail can deliver a top-five personalized technology briefing every morning.

## First-party sources

The default source registry includes OpenAI, Anthropic, Google AI, Google DeepMind, Google Research, Google Developers, Microsoft, Microsoft Developer Blogs, Microsoft Research, Microsoft Azure, Meta AI, Mistral AI, Cohere, xAI, NVIDIA Developer, AWS What's New, AWS Machine Learning, GitHub Changelog, GitHub Blog, Cloudflare, Supabase, Vercel, Docker, Kubernetes, PyTorch, Hugging Face, JetBrains, Mozilla, Chromium, Apple Newsroom, Apple Machine Learning Research, AMD, Intel, Qualcomm and Samsung Newsroom.

Some first-party sites may return 403/rate limits to serverless clients. The app does not treat those failures as fatal; broad discovery feeds and Google News queries remain secondary coverage paths.

## Research

Research collection uses arXiv, Hugging Face Daily Papers, Semantic Scholar, OpenAlex, Crossref, plus research/news pages from OpenAI, Anthropic, Google Research, Google DeepMind, Microsoft Research, Meta AI, NVIDIA, Apple ML Research, Mistral and Cohere.

## Gmail morning digest

After Gmail is connected, **news ingestion does not send email immediately** in production. Instead, the scheduled briefing selects the top 5 relevant technology stories for the user's enabled interests and emails them every morning.

Selection behavior:

- Only technology articles are ingested.
- Articles receive a relevance score from the enabled interests/keywords.
- The digest looks at articles ingested since the previous successful digest; on the first run it looks back over the last 24 hours.
- It takes the highest-scoring stories first and falls back to lower positive scores if fewer than 5 pass the threshold.
- A successful digest is recorded by IST calendar date, preventing duplicate sends if the cron request is retried.

Default schedule: `03:30 UTC`, which corresponds to `09:00 IST`. On Vercel Hobby, cron jobs can run within a one-hour window; Pro provides minute-level scheduling.

## Vercel deployment

Vercel supports FastAPI directly with its Python runtime; this project exports the FastAPI app from the root `main.py` entrypoint. Use Vercel CLI or connect the repository in the dashboard. See the current Vercel FastAPI documentation: https://vercel.com/docs/frameworks/backend/fastapi

### 1. Create Neon Postgres

Vercel's current PostgreSQL marketplace integration uses Neon. Create a Neon database from the Vercel Marketplace/Storage UI and copy its `DATABASE_URL`. Use a pooled connection URL where available. The Vercel Marketplace currently lists Neon starting at $0.

Set `DATABASE_URL` in Vercel Project Settings → Environment Variables. The app converts `postgresql://` / `postgres://` to `postgresql+psycopg://` automatically.

### 2. Configure Gmail OAuth

Use a Google OAuth Web client. Register:

`https://YOUR-DOMAIN.vercel.app/api/auth/gmail/callback`

Set `GOOGLE_REDIRECT_URI` to exactly the same URL. Put the OAuth client JSON into the `GOOGLE_CLIENT_SECRET_JSON` Vercel secret as a single line.

Local development may still use `credentials.json` instead.

### 3. Vercel environment variables

At minimum:

```text
SECRET_KEY
CRON_SECRET
DATABASE_URL
GOOGLE_CLIENT_SECRET_JSON
GOOGLE_REDIRECT_URI
APP_BASE_URL
NOTIFICATION_RECIPIENT
ENABLE_SCHEDULER=false
INITIAL_FETCH_ON_STARTUP=false
```

Also add any optional API keys from `.env.production.example`.

### 4. Deploy

```bash
npm install -g vercel
vercel login
vercel
vercel --prod
```

The current Vercel CLI requirement for FastAPI is 48.1.8 or newer.

### 5. Test the deployment

```bash
curl https://YOUR-DOMAIN.vercel.app/api/health
```

Open:

`https://YOUR-DOMAIN.vercel.app`

Then connect Gmail from the hidden settings drawer.

## Fresh ingestion on Vercel Hobby

Vercel Hobby Cron is limited to once-daily schedules. Therefore the included `vercel.json` contains only the 09:00 IST morning digest cron. To keep the news database fresh throughout the day without upgrading Vercel, the project also includes:

`.github/workflows/news-ingest.yml`

That workflow calls the protected ingestion endpoints every 10 minutes. Add these GitHub repository secrets:

```text
VERCEL_APP_URL=https://YOUR-DOMAIN.vercel.app
CRON_SECRET=<same value configured in Vercel CRON_SECRET>
```

GitHub-hosted scheduled jobs can experience scheduling delays, so this is a best-effort 10-minute polling loop rather than a strict real-time guarantee.

### Vercel Pro option

If you use Vercel Pro, you can replace the GitHub Action with additional Vercel Cron entries for the ingestion endpoints. Vercel currently documents minute-level scheduling on Pro and daily-only scheduling on Hobby.

## Cron security

Vercel sends the value of `CRON_SECRET` as a Bearer token in the Authorization header for scheduled cron requests. The application accepts that header and also accepts `X-Cron-Token` for local/CI calls.

## Local development

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
uvicorn app.main:app --reload
```

For Vercel-like local behavior, install the Vercel CLI and run `vercel dev`.

## Important

Vercel serverless functions do not provide a persistent local filesystem, so do not use the bundled SQLite file for production. Use Neon/Postgres. The built-in APScheduler is automatically disabled when the `VERCEL` environment variable is present.

No free combination of feeds can guarantee literally every tech announcement. Publisher blocking, indexing delays and non-feed publishing can create gaps. First-party sources are therefore prioritized, with multiple independent discovery layers as backup.
