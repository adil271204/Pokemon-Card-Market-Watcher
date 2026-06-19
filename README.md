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
| `EBAY_CLIENT_ID` | If real API | – | eBay Developer App Client ID |
| `EBAY_CLIENT_SECRET` | If real API | – | eBay Developer App Client Secret |
| `EBAY_MARKETPLACE` | No | `EBAY_DE` | eBay marketplace ID (`EBAY_DE`, `EBAY_US`, …) |
| `EBAY_ENV` | No | `production` | `production` or `sandbox` |
| `EBAY_SEARCH_LIMIT` | No | `50` | Listings fetched per page (max 200) |
| `EBAY_LOOKBACK_DAYS` | No | `14` | Backfill: how many days back to look |
| `EBAY_MAX_PAGES` | No | `5` | Backfill: max API pages per search |
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

## 11. eBay Developer Account Setup

To use real eBay listings (`USE_MOCK_EBAY=false`), you need a free eBay Developer account:

1. Go to [developer.ebay.com](https://developer.ebay.com) and sign in with your eBay account (or create one).
2. Navigate to **My Account → Application Keysets**.
3. Click **Create a Keyset** → choose **Production**.
4. Copy **App ID (Client ID)** → set as `EBAY_CLIENT_ID`.
5. Copy **Cert ID (Client Secret)** → set as `EBAY_CLIENT_SECRET`.
6. Under **User Tokens**, make sure the **Browse API** scope (`https://api.ebay.com/oauth/api_scope`) is enabled. For the Client Credentials Flow (no user login), this scope is allowed by default.
7. Set `EBAY_ENV=production` and `USE_MOCK_EBAY=false` in your environment.

> **Sandbox:** If you want to test without real data, create a **Sandbox** keyset instead and set `EBAY_ENV=sandbox`. Sandbox listings are fake.

> **Cost:** The eBay Browse API is free for personal/developer use within the standard call limits.

---

## 12. Normaler Watcher vs. Backfill

### A) Normaler Cron-Watcher (`main.py --once`)
- Läuft alle 10 Minuten als Render Cron Job
- Sucht die **neuesten** Listings für jede aktive Watchlist (1 Seite)
- Klassifiziert und bewertet Deals
- Sendet Telegram-Alerts bei guten Deals
- Speichert neue Listings in der Datenbank

### B) Backfill / Letzte 14 Tage laden
- Wird manuell über das Dashboard ausgelöst
- Lädt Listings der letzten `EBAY_LOOKBACK_DAYS` Tage via Pagination
- Speichert neue Listings, überspringt bereits bekannte
- **Sendet keine Telegram-Alerts**
- Nützlich beim ersten Start oder nach längerer Pause

**Wo im Dashboard:**
- Watchlists-Seite → Uhr-Symbol 🕐 pro Watchlist → einzelnen Backfill starten
- Übersicht → „Alle Watchlists: letzte 14 Tage laden" → globaler Backfill

**Lokaler Test:**
```bash
# Einzelne Watchlist testen (Mock-Modus)
USE_MOCK_EBAY=true python -c "
from src.ebay_client import EbayClient
c = EbayClient()
listings = c.search_recent_listings('Umbreon VMAX', lookback_days=14)
for l in listings:
    print(l.ebay_item_id, l.listing_date, l.title[:40])
"
```

---

## 13. Telegram Bot Setup

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

## 13. Länderfilter (EU-only, kein UK)

Standardmäßig werden nur Listings aus EU-Ländern gespeichert und für Alerts berücksichtigt.

**Warum kein UK?**
Nach dem Brexit fallen bei Lieferungen aus Großbritannien Einfuhrabgaben an. UK/GB ist daher standardmäßig ausgeschlossen.

**Wie funktioniert der Filter?**
Der Filter basiert auf dem Feld `itemLocation.country` der offiziellen eBay Browse API (`GET /buy/browse/v1/item_summary/search`). Es wird kein Scraping verwendet.

| Variable | Standard | Bedeutung |
|---|---|---|
| `EBAY_ALLOWED_COUNTRIES` | alle EU-Länder | Nur diese Länder werden gespeichert |
| `EBAY_EXCLUDED_COUNTRIES` | `GB,UK,US,CN,JP,CA,AU` | Diese Länder werden immer abgelehnt |
| `EBAY_ALLOW_UNKNOWN_LOCATION` | `false` | Listings ohne Standort ablehnen |

**Migration (einmalig):**
```bash
python scripts/migrate_listing_location_fields.py
```
Auf Render läuft die Migration automatisch beim Deploy.

---

## 14. Listings ausblenden (Soft Delete)

Listings werden **niemals physisch gelöscht**. Stattdessen wird ein `deleted_at`-Timestamp gesetzt. Ausgeblendete Listings:

- verschwinden aus Overview, Listings-Seite und Analytics
- bleiben in der Datenbank erhalten (wiederherstellbar)
- beeinflussen keine Alerts

**Wie ausblenden:**
- Listings-Seite → Auge-Symbol 🙈 pro Zeile
- Listings-Seite → „Alle angezeigten ausblenden"-Button
- Overview → „Alle ausblenden"-Button im Neueste-Listings-Block

**Migration (einmalig bei neuer Instanz):**
```bash
python scripts/migrate_seenlisting_deleted_at.py
```
Auf Render läuft die Migration automatisch beim Deploy über den `buildCommand`.

---

## 15. Set Scanner

Analysiert alle Karten eines Pokémon-Sets auf Grading-Opportunities.

**Dashboard → Sets** (`/sets`)

### A) Set importieren

CSV hochladen unter **Sets → CSV importieren** (`/sets/import`).

**CSV-Format:**

```csv
set_name,set_code,language,card_name,card_number,rarity,variant
Scarlet & Violet 151,sv151,EN,Charizard ex,006/165,Double Rare,normal
Scarlet & Violet 151,sv151,EN,Mew ex,193/165,Special Illustration Rare,normal
```

| Spalte | Pflicht | Beispiel |
|---|---|---|
| `set_name` | ✓ | `Scarlet & Violet 151` |
| `set_code` | ✓ | `sv151` |
| `language` | ✓ | `EN` |
| `card_name` | ✓ | `Charizard ex` |
| `card_number` | ✓ | `006/165` |
| `rarity` | – | `Double Rare` |
| `variant` | – | `normal` |

Duplikate (gleicher set_code + language + card_number) werden automatisch übersprungen.

Eine Beispiel-CSV ist unter `examples/set_import_template.csv` enthalten.

### B) Set scannen

1. **Dashboard → Sets → Set öffnen → „Set scannen"**
2. Das System sucht für jede Karte Raw-, PSA 9- und PSA 10-Listings über die eBay Browse API.
3. Ergebnisse erscheinen unter **Ergebnisse** als Ranking.

**Für große Sets (> 50 Karten) empfohlen – CLI-Variante:**
```bash
python scripts/run_set_scan.py --set-code sv151
python scripts/run_set_scan.py --set-code sv151 --max-cards 50 --days 30
```

Als Render One-off Job:
```
python scripts/run_set_scan.py --set-code sv151
```

### C) Ranking & Grading-Berechnung

Pro Karte berechnet das System:

| Metrik | Bedeutung |
|---|---|
| Raw Median | Median-Preis aktiver Raw-Listings |
| PSA 9 / PSA 10 Median | Median-Preis aktiver PSA-Listings |
| PSA10-Multiplikator | PSA 10 / Raw Median |
| Erwarteter Gewinn | Gewinn nach Grading-Kosten, Versand, Marketplace-Fee |
| ROI % | Return on Investment |
| Rating | Sehr interessant / Interessant / Riskant / … |

**Rating-Stufen:**
- `Sehr interessant` – hoher erwarteter Profit
- `Interessant` – moderater Profit
- `Riskant` – knapper oder negativer Erwartungswert
- `Nur bei PSA 10 interessant` – nur PSA 10 wäre profitabel
- `Nicht attraktiv` – kein Profit zu erwarten
- `Zu wenig Daten` – zu wenige Listings für eine Einschätzung

### D) Environment Variables (Set Scanner)

| Variable | Standard | Bedeutung |
|---|---|---|
| `SET_SCAN_MAX_CARDS` | `200` | Max. Karten pro Scan |
| `SET_SCAN_INCLUDE_AUCTIONS` | `false` | Auktionen in Preisberechnung einschließen |
| `SET_SCAN_DAYS` | `14` | Lookback-Zeitraum in Tagen |
| `GRADING_COST` | `18` | PSA-Grading-Gebühr in € |
| `GRADING_SHIPPING_TO_GRADER` | `15` | Versand zum Grader in € |
| `GRADING_RETURN_SHIPPING` | `15` | Rückversand in € |
| `GRADING_MARKETPLACE_FEE_PERCENT` | `13` | Marktplatz-Gebühr in % |
| `GRADING_RISK_DISCOUNT_PERCENT` | `10` | Risikoabschlag in % |
| `GRADING_PSA10_PROBABILITY` | `0.50` | Wahrscheinlichkeit PSA 10 |
| `GRADING_PSA9_PROBABILITY` | `0.30` | Wahrscheinlichkeit PSA 9 |
| `GRADING_PSA8_OR_LOWER_PROBABILITY` | `0.20` | Wahrscheinlichkeit PSA 8 oder niedriger |

### E) Einschränkungen

- **Aktive Listings, keine Verkaufspreise.** Aktive Preise können überhöht sein und spiegeln nicht den tatsächlichen Marktpreis wider.
- **eBay Browse API ≠ eBay-Websuche.** API-Ergebnisse können von der eBay-Website abweichen.
- **Kein Scraping.** Ausschließlich die offizielle eBay Browse API wird verwendet.
- **Keine Garantie für Profit.** Das Rating ist eine Einschätzung, keine Anlageberatung.
- Grading-Wahrscheinlichkeiten sind konfigurierbare Annahmen, keine garantierten Quoten.

---

## 16. Kartenlisten-Import per URL

Unter **Sets → URL-Import** (`/sets/import-url`) kannst du eine externe Kartenlisten-Seite per URL importieren.

### Ablauf

1. `/sets/import-url` öffnen
2. URL der Kartenliste einfügen (z. B. `https://www.cardsrfun.de/collections/sv151`)
3. Optional: Set Name, Set Code, Sprache, Quellenname eingeben
4. „Kartenliste prüfen" klicken → Vorschau erscheint
5. Karten einzeln ab- oder anwählen, Felder direkt editieren
6. „Ausgewählte Karten importieren" klicken
7. Set scannen unter `/sets/{id}`

### Wie der Parser funktioniert

Das System versucht nacheinander:
1. JSON-LD / eingebettete JSON-Daten (`script[type="application/ld+json"]`, `__NEXT_DATA__`)
2. Strukturierte HTML-Tabellen (mit Header-Erkennung für Name, Nummer, Rarität)
3. HTML-Listen (`<ul>`, `<ol>`)
4. Grid-/Card-Elemente (`class` mit „card", „item", „product")
5. Text-basierter Regex-Fallback (scannt alle sichtbaren Zeilen nach Mustern wie `001/165`)

Jeder erkannten Karte wird ein Confidence-Wert (0–100 %) zugewiesen.

### Unterstützte Seiten

Spezialisierte Parser vorhanden für:
- **cardsrfun.de** – Tabellen und Card-Elemente
- **bulbapedia.bulbagarden.net** – Wikitable
- **serebii.net** – Tabellen
- **pkmncards.com** – Kartenverlinkungen

Der generische Parser funktioniert auf vielen weiteren Seiten, sofern die Karten-nummern (`001/165`) im HTML-Text enthalten sind.

### Einschränkungen

- **JavaScript-gerenderte Seiten (SPA)** können nicht gelesen werden — der Parser sieht nur das initiale HTML.
- **Login-geschützte Seiten** werden nicht unterstützt (kein Login-Bypass).
- **Captcha-Seiten** werden abgelehnt.
- **eBay wird nicht gescraped.** Dieser Import gilt ausschließlich für externe Kartenlisten-Seiten. eBay wird weiterhin nur über die offizielle Browse API abgefragt.
- Der **CSV-Import** bleibt der zuverlässigste Weg, wenn der URL-Parser scheitert.

### Sicherheit (SSRF-Schutz)

- Nur `http://` und `https://` erlaubt
- Keine privaten IP-Adressen (10.x, 172.16.x, 192.168.x, 127.x, 169.254.x)
- Keine `localhost`-Anfragen
- Kein Zugriff auf Cloud-Metadaten-Endpunkte
- Maximale HTML-Größe: 5 MB
- Timeout: 15 Sekunden
- Single GET-Request — kein aggressives Crawling

---

## 17. Listing-Diagnose

Wenn ein Listing nicht im Dashboard erscheint, hilft die Diagnose-Seite:

**Dashboard → Diagnose** (`/diagnostics`)

### A) Listing prüfen
1. eBay-URL oder Item-ID eingeben (z. B. `https://www.ebay.de/itm/123456789012`)
2. Optionale Watchlist auswählen
3. „Listing prüfen" klicken

**Was gezeigt wird:**
- Ist das Listing über die eBay Browse API abrufbar? (404 = abgelaufen/entfernt)
- Ist es in der Datenbank gespeichert? Wurde es ausgeblendet (`deleted_at`)?
- Filterprüfung in 7 Schritten: Query-Match, Preis, Länderfilter, Kaufoption, Keyword-Cleaner, Grade-Filter, Soft Delete
- Wenn ausgeblendet: direkte Wiederherstellen-Schaltfläche

> **Hinweis:** Die Diagnose nutzt den **eBay Item Lookup** (`GET /buy/browse/v1/item/{item_id}`) – einen anderen Endpunkt als die normale Suche (`/item_summary/search`). Ein Listing kann im Item Lookup abrufbar sein, aber in der Suche nicht erscheinen (z. B. wegen Query-Relevanz oder Paginierungstiefe).

### B) Watchlist-Suche debuggen
- Watchlist auswählen → „Suche debuggen"
- Zeigt alle von der API zurückgegebenen Listings mit Status: Neu / Bereits bekannt / Länder-gesperrt

**Kein Scraping:** Alle Daten kommen ausschließlich von der offiziellen eBay Browse API.

---

## 17. Future Enhancements

| Feature | Description |
|---|---|
| **Real eBay Browse API** | ✅ Implemented – OAuth 2.0 Client Credentials with token caching |
| **Sold-Data Module** | Pull actual sold prices from eBay Terapeak or eBay's completed listings to validate `target_market_price` automatically |
| **Web Dashboard** | Simple Flask/FastAPI UI to manage watchlists and view alerts |
| **Price History** | Track price trends for monitored cards over time |
| **Multi-Language Support** | Filter or prioritise listings by card language (English, Japanese, German) |
| **Admin UI** | CRUD interface for watchlists without touching the database directly |
| **Alembic Migrations** | Replace `create_all` with proper schema migrations for production updates |
| **Multiple Marketplaces** | Extend to Cardmarket, TCGPlayer, or other platforms |
