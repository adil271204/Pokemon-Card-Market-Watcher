# Pokemon Card Market Watcher

A Python-based cron tool that watches eBay for newly listed Pokémon cards,
scores deals against a configured market value, and fires Telegram alerts with
a direct eBay link when a good deal is found.

---

## Table of Contents

1. [Project Description](#1-project-description)
2. [Local Setup](#2-local-setup)
3. [GitHub Setup](#3-github-setup)
4. [Render Setup](#4-render-setup)
5. [Environment Variables](#5-environment-variables)
6. [PostgreSQL Note](#6-postgresql-note)
7. [Initialize the Database](#7-initialize-the-database)
8. [Add a Watchlist Entry](#8-add-a-watchlist-entry)
9. [Run Locally (one cycle)](#9-run-locally-one-cycle)
10. [Deploy via Render Blueprint](#10-deploy-via-render-blueprint)
11. [Telegram Bot Setup](#11-telegram-bot-setup)
12. [Important: Active vs. Sold Listings](#12-important-active-vs-sold-listings)
13. [Future Enhancements](#13-future-enhancements)

---

## 1. Project Description

`pokemon-card-market-watcher` monitors eBay for newly listed Pokémon cards that
match configurable search queries (Watchlists). For each match it:

- Skips listings already seen (deduplicated via PostgreSQL).
- Classifies the listing title (proxy, reprint, wrong grade, etc.).
- Calculates total price (item + shipping).
- Compares against a configured market value.
- Scores the deal (0–100).
- Sends a Telegram message with a direct eBay link if the score is ≥ 70 and
  the discount is above the configured minimum.

The watcher runs on Render as a **Cron Job** every 10 minutes.

---

## 2. Local Setup

### Requirements

- Python 3.11+
- `pip`
- A PostgreSQL instance (or use the SQLite fallback for quick tests)

### Steps

```bash
# 1. Clone the repository
git clone https://github.com/YOUR_USERNAME/pokemon-card-market-watcher.git
cd pokemon-card-market-watcher

# 2. Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Copy the example .env and fill in your values
cp .env.example .env
# Open .env in your editor and set at minimum DATABASE_URL
```

For quick local tests without PostgreSQL, leave `DATABASE_URL` empty in `.env`.
The app will fall back to a local SQLite file `pokemon_watcher.db`.

---

## 3. GitHub Setup

```bash
# Inside the project directory
git init
git add .
git commit -m "Initial commit"

# Create a new repo on GitHub (via browser or gh CLI)
gh repo create pokemon-card-market-watcher --public --source=. --remote=origin --push
# OR manually:
git remote add origin https://github.com/YOUR_USERNAME/pokemon-card-market-watcher.git
git branch -M main
git push -u origin main
```

---

## 4. Render Setup

### Option A – Render Blueprint (recommended)

1. Go to [render.com](https://render.com) → **New** → **Blueprint**.
2. Connect your GitHub account and select the `pokemon-card-market-watcher` repo.
3. Render reads `render.yaml` automatically and creates:
   - A **PostgreSQL** database (`pokemon-watcher-db`).
   - A **Cron Job** that runs `python main.py` every 10 minutes.
4. After the first deploy, set the secret environment variables in the Render
   dashboard (see Section 5).

### Option B – Manual

1. Create a **PostgreSQL** instance on Render (free tier available).
2. Create a **Cron Job** service:
   - **Runtime**: Python
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `python main.py`
   - **Schedule**: `*/10 * * * *`
3. Link `DATABASE_URL` from the Postgres instance (Render does this
   automatically in Blueprint mode).

---

## 5. Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `DATABASE_URL` | Yes (prod) | SQLite fallback | Full PostgreSQL connection string |
| `USE_MOCK_EBAY` | No | `true` | Set to `false` to use the real eBay API |
| `EBAY_CLIENT_ID` | Only if real API | – | eBay Developer App Client ID |
| `EBAY_CLIENT_SECRET` | Only if real API | – | eBay Developer App Client Secret |
| `EBAY_MARKETPLACE` | No | `EBAY_DE` | eBay marketplace ID |
| `TELEGRAM_BOT_TOKEN` | No | – | Telegram Bot token from @BotFather |
| `TELEGRAM_CHAT_ID` | No | – | Your Telegram chat/user ID |
| `LOG_LEVEL` | No | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |

Set **secret** variables (`EBAY_CLIENT_ID`, `EBAY_CLIENT_SECRET`,
`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`) via the Render dashboard **→
Environment → Add Environment Variable**. Never commit them to Git.

---

## 6. PostgreSQL Note

Render Cron Jobs have **no persistent disk**. SQLite databases (file-based)
are therefore unsuitable for production – each cron invocation starts with a
fresh container that has no memory of previous runs.

This project uses **PostgreSQL** as the production database via `DATABASE_URL`.
The `SeenListing` table acts as the deduplication store across runs.

Render provides the connection string in the format `postgres://...`.
The app automatically rewrites it to `postgresql://...` as required by
SQLAlchemy.

---

## 7. Initialize the Database

Run this **once** after setting up your database. It creates all tables using
`SQLAlchemy`'s `create_all` (no Alembic migrations needed for the MVP).

```bash
python scripts/init_db.py
```

On Render you can run this as a one-off job via the Render Shell, or add it
temporarily as the build command:

```
pip install -r requirements.txt && python scripts/init_db.py
```

---

## 8. Add a Watchlist Entry

The example below adds an **Umbreon VMAX Alt Art PSA 10** watchlist:

```bash
python scripts/add_watchlist.py
```

It is idempotent – running it twice will not create duplicates.

To add custom watchlists, open `scripts/add_watchlist.py` and edit the
`EXAMPLE_WATCHLIST` dict, or write your own script that imports `Watchlist`
from `src.models`.

---

## 9. Run Locally (one cycle)

```bash
# Make sure .env is configured and the DB is initialised
python main.py
# or
python scripts/run_once.py
```

With `USE_MOCK_EBAY=true` (the default) you will see:

- `mock-001` → Umbreon PSA 10 at €1 162 → **alert fired** (~23% discount)
- `mock-002` → Proxy → filtered out (bad keyword)
- `mock-003` → PSA 9 → filtered out (grade mismatch for a PSA-10 watchlist)
- `mock-004` → €1 815 → filtered out (price above market value)

---

## 10. Deploy via Render Blueprint

```bash
# Push your code to GitHub
git push origin main
```

Then in Render:

1. **New → Blueprint → Connect repo**.
2. Render detects `render.yaml` and provisions DB + Cron Job.
3. Go to the Cron Job service → **Environment** and add the secret variables
   that are not in `render.yaml` (Telegram tokens, eBay keys).
4. Trigger a manual run via **Render → Cron Job → Run now** to verify.

> **Blueprint note:** Render's free-tier Cron Job minimum schedule is
> `*/1 * * * *` (every minute). The `render.yaml` uses `*/10 * * * *`.
> If Render rejects the schedule, change it to `*/15 * * * *` or configure
> it manually in the dashboard after blueprint deployment.

---

## 11. Telegram Bot Setup

1. Open Telegram and message **@BotFather**.
2. Send `/newbot` and follow the prompts. Copy the **HTTP API token**.
3. Start a chat with your new bot, then visit:
   ```
   https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates
   ```
   Send a message to the bot first, then reload the URL. Find `"chat": {"id": ...}`
   in the JSON response – that is your `TELEGRAM_CHAT_ID`.
4. Set both values in Render's Environment panel.

If `TELEGRAM_BOT_TOKEN` is not set, deal alerts are printed to stdout (visible
in Render's log viewer) instead.

---

## 12. Important: Active vs. Sold Listings

This tool watches **active (Buy It Now) listings** – cards that are currently
for sale, but have not necessarily sold. This means:

- A seller can list a card at any price they choose.
- The configured `target_market_price` is set by **you** based on your
  research (e.g. recent sold prices on eBay, PSA Price Guide, Cardmarket).
- The watcher does **not** verify whether the market price is accurate.
- A low-priced listing is not guaranteed to be a real deal if your market
  value is wrong.

**For accurate deal detection** you need sold-listing data (eBay Terapeak,
the eBay Sold filter, or a third-party price database). That is planned as a
future enhancement.

---

## 13. Future Enhancements

| Feature | Description |
|---|---|
| **Real eBay Browse API** | Replace mock mode with live API calls using OAuth Client Credentials |
| **Sold-Data Module** | Pull actual sold prices from eBay Terapeak or eBay's completed listings to validate `target_market_price` automatically |
| **Web Dashboard** | Simple Flask/FastAPI UI to manage watchlists and view alerts |
| **Price History** | Track price trends for monitored cards over time |
| **Multi-Language Support** | Filter or prioritise listings by card language (English, Japanese, German) |
| **Admin UI** | CRUD interface for watchlists without touching the database directly |
| **Alembic Migrations** | Replace `create_all` with proper schema migrations for production updates |
| **Multiple Marketplaces** | Extend to Cardmarket, TCGPlayer, or other platforms |
