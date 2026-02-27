# CostPilot 💰

**Real-time AI spend monitoring dashboard.** Track costs across OpenAI, Anthropic, and custom providers with exact per-token granularity.

> Bloomberg Terminal aesthetic. Live SSE updates. 50+ API endpoints. Ships in 30 seconds.

---

## Features

- **Exact cost tracking** — reads directly from API response logs, not estimates
- **Full token breakdown** — input / output / cache-read / cache-write per event
- **Bloomberg Terminal aesthetic** — dark terminal UI with live data
- **Live SSE updates** — dashboard refreshes in real time via Server-Sent Events
- **50+ API endpoints** — query, filter, export, annotate, compare, and more
- **Multi-provider support** — OpenClaw/Anthropic, Anthropic CSV import, OpenAI Usage API
- **Bearer token auth** — auto-generated on first run, configurable in `config.json`
- **Daily/weekly analytics** — charts, trend detection, anomaly alerts, cache savings
- **Export** — CSV, JSON, Markdown reports
- **Backup & restore** — automatic backup rotation

---

## Quick Start

```bash
# 1. Clone or download
git clone https://github.com/yourorg/costpilot.git
cd costpilot

# 2. Install dependencies
pip install -r requirements.txt

# 3. (Optional) Copy and edit config
cp config.example.json config.json
$EDITOR config.json

# 4. Start the logger (run once, or add to cron)
python3 auto_logger.py

# 5. Start the server
python3 server.py

# Dashboard is at http://localhost:8742/
```

For local development without auth:

```bash
python3 server.py --no-auth
```

---

## Input Modes

CostPilot supports three input modes for ingesting AI cost data.

### `--mode openclaw` (default)

Reads OpenClaw session JSONL files. Each message with a `usage.cost.total` field becomes one cost event, attributed to its own timestamp.

```bash
# Default: reads from ~/.openclaw/agents/main/sessions/
python3 auto_logger.py

# Custom sessions directory
python3 auto_logger.py --sessions-dir /path/to/sessions/

# Or set sessions_dir in config.json
```

JSONL format expected per line:
```json
{
  "timestamp": "2026-02-27T10:00:00Z",
  "message": {
    "model": "claude-sonnet-4-6",
    "usage": {
      "input": 1234,
      "output": 456,
      "cacheRead": 100,
      "cacheWrite": 50,
      "cost": { "total": 0.00234 }
    }
  }
}
```

### `--mode csv`

Import costs from an Anthropic-exported CSV file.

```bash
python3 auto_logger.py --mode csv --csv-file anthropic_export.csv
```

Expected columns:

| Column | Description |
|--------|-------------|
| `date` | `YYYY-MM-DD` |
| `input_tokens` | Input token count |
| `output_tokens` | Output token count |
| `cache_creation_input_tokens` | Cache write tokens |
| `cache_read_input_tokens` | Cache read tokens |
| `cost` | Total cost in USD |

Each row becomes one cost event at midnight UTC of the given date.

### `--mode openai`

Fetch costs from the OpenAI Usage API for a given date.

```bash
# Uses OPENAI_API_KEY env var
python3 auto_logger.py --mode openai

# Specific date
python3 auto_logger.py --mode openai --date 2026-02-27

# Explicit key
python3 auto_logger.py --mode openai --openai-api-key sk-...
```

API key resolution order:
1. `--openai-api-key` CLI argument
2. `OPENAI_API_KEY` environment variable
3. `openai_api_key` field in `config.json`

Default pricing (override in `config.json` under `openai_pricing`):

| Model | Input ($/M) | Output ($/M) | Cache Read ($/M) |
|-------|------------|-------------|-----------------|
| gpt-4o | $2.50 | $10.00 | $1.25 |
| gpt-4o-mini | $0.15 | $0.60 | $0.075 |

---

## Auth

By default, CostPilot protects all `/api/*` endpoints (except `/api/ping` and `/api/health`) with a Bearer token.

**On first run**, a random token is auto-generated, printed to console, and saved to `config.json`:

```
🔑 Bearer token: a3f7b2c9d4e1f8a2b6c0d5e3f1a8b7c4e2d9f5a1b3c8d7e4f2a9b0c6
```

**Using the token:**

```bash
# Header
curl -H "Authorization: Bearer <token>" http://localhost:8742/api/data

# Query param
curl http://localhost:8742/api/data?token=<token>
```

**Configuration:**

```json
{
  "token": "your-custom-token-here"
}
```

Set `"token": ""` to auto-generate. Use `--no-auth` to disable entirely for local dev:

```bash
python3 server.py --no-auth
```

**Exempt endpoints** (no auth required):
- `GET /` — dashboard
- `GET /manifest.json`
- `GET /api/ping`
- `GET /api/health`

---

## Configuration

Copy `config.example.json` to `config.json` and edit:

```json
{
  "dashboard_title": "CostPilot",
  "token": "",
  "sessions_dir": "",
  "mode": "openclaw",
  "openai_api_key": "",
  "openai_pricing": {
    "gpt-4o": {"input": 2.50, "output": 10.0, "cache_read": 1.25}
  },
  "anthropic_pricing": {
    "claude-sonnet-4-6": {"input": 3.0, "output": 15.0, "cache_read": 0.30, "cache_write": 3.75}
  },
  "alert_threshold_usd": 10.0,
  "timezone": "UTC",
  "refresh_interval_sec": 2
}
```

---

## API Reference

All endpoints return JSON unless noted. Auth required on `/api/*` (except ping/health).

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Dashboard HTML |
| `GET` | `/api/data` | Full analytics state |
| `GET` | `/api/live` | SSE stream (real-time) |
| `GET` | `/api/events` | Paginated raw events (`?page=&page_size=&from=&to=`) |
| `GET` | `/api/stats` | Aggregate statistics |
| `GET` | `/api/health` | Health check (no auth) |
| `GET` | `/api/ping` | Trivial 200 OK (no auth) |
| `GET` | `/api/version` | Version info |
| `GET` | `/api/export` | Export data (`?format=csv\|json\|markdown`) |
| `GET` | `/api/config` | Current config |
| `POST` | `/api/config` | Update config fields |
| `POST` | `/api/import` | Import JSONL events (dedup + validate) |
| `GET` | `/api/sessions` | All tracked session keys |
| `GET` | `/api/timeline` | Day timeline (`?date=YYYY-MM-DD`) |
| `GET` | `/api/compare` | Compare two tasks (`?task1=X&task2=Y`) |
| `GET` | `/api/report` | Weekly report (`?format=markdown`) |
| `GET` | `/api/estimate` | Cost estimate (`?model=X&input_tokens=N&output_tokens=M`) |
| `GET` | `/api/annotations` | List annotations |
| `POST` | `/api/annotations` | Add annotation |
| `DELETE` | `/api/annotations/{id}` | Delete annotation |
| `PATCH` | `/api/events/{hash}/rename` | Rename task |
| `DELETE` | `/api/clear` | Clear all events |
| `GET` | `/api/backups` | List backups |
| `POST` | `/api/restore` | Restore from backup |
| `GET` | `/api/docs` | Full endpoint schema |
| `GET` | `/api/autologger-health` | Auto-logger last run status |

---

## Auto-Logger Options

```
python3 auto_logger.py [OPTIONS]

Options:
  --mode {openclaw,csv,openai}   Input mode (default: openclaw)
  --sessions-dir DIR             Sessions directory (openclaw mode)
  --csv-file FILE                CSV file path (csv mode)
  --openai-api-key KEY           OpenAI API key (openai mode)
  --date YYYY-MM-DD              Date to fetch (openai mode, default: today)
  --output-file FILE             Output JSONL file (default: cost-events.jsonl)
  --config-file FILE             Config file (default: config.json)
  --dry-run                      Preview without writing
  --reset-state                  Clear state (re-process all data)
  --summary                      Print daily cost summary and exit
  --export-csv FILE              Export events to CSV file
  --verbose                      Verbose output
  --quiet                        Suppress output
```

---

## Cron Setup

Add to crontab to run every 5 minutes:

```bash
*/5 * * * * cd /path/to/costpilot && python3 auto_logger.py --quiet
```

---

## Docker

```bash
docker-compose up -d
```

Dashboard at `http://localhost:8742/`.

---

## License

MIT — see [LICENSE](LICENSE)
