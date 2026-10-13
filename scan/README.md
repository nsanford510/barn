# VPT Scanner - Vacant Property Tax & Delinquency Tracker

A tool for identifying properties subject to Vacant Property Tax (VPT) and properties delinquent on their taxes in Alameda County, CA. Includes PG&E power status checking to help identify potentially vacant properties.

## Features

- **Multi-City Scanning**: Scans properties across Oakland, Berkeley, and other Alameda County cities
- **VPT Detection**: Identifies properties with Vacant Property Tax markers
  - Oakland: MEAS-W (Measure W)
  - Berkeley: MEAS-M (Measure M)
- **Delinquency Detection**: Flags properties with actual tax delinquency (defaulted taxes, redemption amounts due)
- **PG&E Power Status**: Checks if power is on/off for each property (indicator of vacancy)
- **Web Interface**: Browse, filter, and map properties with an interactive UI
- **Real-time Syncing**: Optional sync to Supabase for cloud backup
- **Public Access**: Cloudflare tunnel support for secure remote access

## Project Structure

```
vpt/
├── run_all.py              # Main orchestrator - local scanner + web UI
├── find_meas_w_addresses.py # Core scanning logic for VPT/delinquency (local)
├── pge_scanner.py          # PG&E power status checker (local, uses Playwright)
├── db.py                   # Supabase database layer (primary DB, used by web UI)
├── webgui/
│   ├── app.py              # Flask web application
│   └── templates/
│       ├── index.html      # List view with filtering
│       ├── map.html        # Interactive map view
│       ├── admin.html      # Scan control panel
│       └── login.html      # Authentication page
├── requirements.txt        # Python dependencies
├── install.sh              # Installation script
├── parcels.csv             # Property parcel backlog (local only, not deployed)
├── .env                    # SUPABASE_URL, SUPABASE_ANON_KEY (required)
├── measw_cache.jsonl       # Scan cache (local only, not deployed)
└── .env                    # Environment variables (not in repo)
```

## Installation

### Prerequisites

- Python 3.10+
- pip

### Setup

```bash
# Clone the repository
git clone <repo-url>
cd vpt

# Run the install script
./install.sh

# Or manually:
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium  # For PG&E power checking
```

### Data File

You need a Parcels CSV file from Alameda County with the following columns:

- `APN` - Assessor's Parcel Number
- `SitusCity` - City name
- `SitusAddress` - Property address
- `CENTROID_X`, `CENTROID_Y` - Coordinates (Web Mercator)

Place it as `parcels.csv` in the project root.

## Usage

### Start the Scanner (Local Only)

```bash
source .venv/bin/activate

# Scan a specific city
python run_all.py --city=OAKLAND

# Run the daily backlog intake + enrichment pass
python intake_autopilot.py

# Continuous scanning (all cities in loop)
python run_all.py --continuous

# Only re-check properties already in the DB (removes ones that stopped paying VPT)
python find_meas_w_addresses.py --city=OAKLAND --recheck-only

# See what a scan would add/remove without touching the DB
python find_meas_w_addresses.py --city=OAKLAND --recheck-only --dry-run
```

The web UI will be available at http://localhost:5000

> Note: For **cloud deployment (Vercel)** only the Flask app (`app.py` + `webgui/`) and `db.py` are used.
> Scanning scripts (`run_all.py`, `find_meas_w_addresses.py`, `pge_scanner.py`, etc.) are intended for **local use only** and are not run on Vercel.

### Environment Variables

Create a `.env` file pointing at your Supabase project:

```env
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_ANON_KEY=your-anon-key
# Optional: service key for server-side scripts
SUPABASE_SERVICE_KEY=your-service-key
```

Authentication uses **Supabase Auth** (email/password). Create users in the [Supabase Dashboard](https://supabase.com/dashboard) under Authentication → Users. The web login page uses `SUPABASE_URL` and `SUPABASE_ANON_KEY` (public, safe in the browser).

For **Gemini Deep Research**, add your Gemini API key:

```env
GOOGLE_API_KEY=your-gemini-key
```

### Vercel deployment

For Vercel, set **Environment Variables** in the project (Settings → Environment Variables). Required:

- `SUPABASE_URL` – your Supabase project URL
- `SUPABASE_ANON_KEY` – used for login page and JWT verification (Vercel integration may inject this)
- `SUPABASE_SERVICE_KEY` (or `SUPABASE_ANON_KEY`) – for server-side DB access

Optional: `SECRET_KEY` (Flask session cookie), `SCOUT_API_KEY` (mobile/Scout app API key).
Auth is **Supabase Auth**; create users in Supabase Dashboard → Authentication → Users.
Mobile API requests can authenticate with either `Authorization: Bearer <supabase_access_token>` or `X-API-Key: <SCOUT_API_KEY>`.

If these are missing, the app returns a 503 with instructions. To verify the deployment:

- **`/health`** – returns `{"status":"ok","db":"connected"}` when Supabase is configured, or `db: not_configured` with a hint when env vars are missing.

### Vacancy research by remote agents

AI agents on other machines research each property and report a vacancy verdict
with evidence. Apply `db_migrations/supa_migration_vacancy_research.sql`, then:

```bash
python research_queue.py add-agent grokbot-1     # prints the agent's token once
python research_queue.py enqueue --city OAKLAND  # queue properties
python research_queue.py status                  # or Scan > Vacancy in the web UI
```

Agents use only the files in `research_agent_kit/` (see its README), never the
secret key.

## Web Interface

### List View (`/`)

- Search by address, APN, parcel number
- Filter by: City, VPT, Delinquent, Power status, Favorites, Zip code
- Sort by any column
- Links to tax bills, Google Maps, Street View

### Map View (`/map`)

- Interactive Leaflet map with marker clustering
- Same filtering options as list view
- Color-coded markers: Green (power on), Red (power off), Gray (unknown)
- Click markers for property details and links

### Admin Panel (`/admin`)

- View current scan status
- Start/stop scans
- View city statistics

## Database Schema

Properties are stored in SQLite with the following key fields:

| Field                    | Description                                  |
| ------------------------ | -------------------------------------------- |
| `apn`                  | Assessor's Parcel Number (primary key)       |
| `location_of_property` | Property address                             |
| `city`                 | City name                                    |
| `has_vpt`              | 1 if property has VPT marker                 |
| `vpt_marker`           | VPT marker and amount (e.g., "MEAS-W OAKLAND VPT $6,000.00") |
| `delinquent`           | 1 if property is tax delinquent              |
| `power_status`         | "on", "off", or "unknown"                    |
| `last_payment`         | Date of last tax payment                     |
| `bill_url`             | Link to tax bill                             |

## How It Works

1. **Scanning**: Reads parcel data from CSV, queries Alameda County tax portal for each APN
2. **VPT Detection**: Checks bill HTML for VPT markers (MEAS-W for Oakland, MEAS-M for Berkeley). **Only Oakland and Berkeley have VPT markers in the scanner;** other cities will show 0 VPT unless you add markers.
3. **Delinquency Detection**: Looks for actual delinquency indicators (redemption amount, prior year amounts due, delinquent status, tax defaulted with date). If you see no delinquent results, the county bill wording may differ; set `VPT_DEBUG_DELINQUENCY=1` in `.env` and run a scan to log near-matches so patterns can be added.
4. **Database Storage**: Only stores properties whose latest bill has a VPT charge ($6,000, or the $3,000 condo/duplex rate). Delinquent-only properties are no longer added.
5. **Power Checking**: Uses Playwright to check PG&E website for power status
6. **Caching**: Verified lookups are cached in `measw_cache.jsonl`. A parcel is looked up again once a newer bill year is due (bills post each October), or after `VPT_RECHECK_DAYS` if its newest bill was older. Failed lookups are never cached.

### Removal of lapsed properties

Each scan first re-checks the properties already in the DB (rows with `has_vpt=1` or `delinquent=1`), then scans unchecked parcels. When a property's latest bill has no VPT charge:

- it is **deleted** from `bills`, or
- if it is curated (on a list/Favorites, has scout results or outreach), it is **kept** and set to `has_vpt=0`, because deleting would cascade to that work.

Rows with neither flag (PG&E power-off promotions, CSV imports) are never touched. Nothing is removed unless the bill was fetched and its parcel number matches the APN. Every removal is appended to `vpt_removed.jsonl`. Use `--dry-run` to preview; `--force` ignores the cache.

### Scan speed

The county site rate-limits per IP and answers "Request Rejected" beyond its limit (measured October 2026: bursts above ~15 lookups/min are blocked at once, and a steady 8–12/min was blocked within about 130 lookups), so the scanner paces itself rather than running in parallel:

| Variable                  | Default | Meaning                                                        |
| ------------------------- | ------- | -------------------------------------------------------------- |
| `VPT_LOOKUP_INTERVAL_SEC` | `10`    | Minimum seconds between lookups (5–8 s was eventually rejected) |
| `VPT_MAX_WORKERS`         | `1`     | Browser workers; more does not help under the per-IP limit      |
| `VPT_RECHECK_DAYS`        | `30`    | Re-check parcels whose newest bill is from an older year        |
| `VPT_ROLL_YEAR`           | auto    | Override the current bill year                                  |

At 6 lookups/min, re-checking ~2,000 DB properties takes about 5.5 hours and a first full pass over Oakland (~94,000 parcels) about 11 days; later runs in the same bill year only look up what is not cached. On a rejection the scan pauses, slows down for the rest of the run, and stops after 5 rejections in a row. The scan page shows parcels scanned / total and the current scanned/min.

## API Endpoints

| Endpoint                 | Method      | Description                                                                         |
| ------------------------ | ----------- | ----------------------------------------------------------------------------------- |
| `/`                    | GET         | List view HTML                                                                      |
| `/map`                 | GET         | Map view HTML                                                                       |
| `/admin`               | GET         | Admin panel HTML                                                                    |
| `/api/markers`         | GET         | Paginated map markers (`items`, `total`, `page`, `page_size`, `has_more`) |
| `/api/scan/status`     | GET         | Current scan status                                                                 |
| `/api/scan/start`      | POST        | Start a scan                                                                        |
| `/api/scan/stop`       | POST        | Stop continuous scan                                                                |
| `/api/favorites`       | GET         | List favorites                                                                      |
| `/api/favorites/<apn>` | POST/DELETE | Add/remove favorite                                                                 |

## License

Private - All rights reserved
