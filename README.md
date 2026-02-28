# CostPilot 💰

**Real-time AI cost tracking dashboard for OpenClaw, Anthropic, and OpenAI.** Most AI agents run blind — CostPilot shows exactly what you're spending, per token, per session, in real time.

---

## What it does

- **Exact cost tracking** — reads directly from API response logs, no estimation or guessing
- **Full token breakdown** — input / output / cache-read / cache-write per event, per session
- **Live dashboard** — Bloomberg Terminal-style dark UI, refreshes in real time via SSE
- **Multi-provider support** — OpenClaw/Anthropic JSONL, Anthropic CSV export, OpenAI Usage API
- **Analytics & alerts** — daily/weekly charts, anomaly detection, cache savings, threshold alerts
- **50+ API endpoints** — query, filter, export (CSV/JSON/Markdown), annotate, compare tasks

---

## Screenshots / Demo

> Dashboard preview coming soon.
> 
> To see it live: follow the Quickstart below — the dashboard loads in seconds.

---

## Quickstart (5 minutes)

```bash
# 1. Clone
git clone https://github.com/RobinvBerg/costpilot.git
cd costpilot

# 2. Install dependencies
pip install -r requirements.txt

# 3. (Optional) Run install script — creates config, checks port, sets permissions
bash install.sh

# 4. Log your cost data (OpenClaw mode by default)
python3 auto_logger.py

# 5. Start the server
python3 server.py

# 6. Open dashboard
open http://localhost:8742
```

For local development without authentication:

```bash
python3 server.py --no-auth
```

> **First run:** A Bearer token is auto-generated and printed to console. Save it — you'll need it for API calls.

---

## How it works

CostPilot has two components:

1. **`auto_logger.py`** — ingests cost data from one of three sources and writes it to a local `cost-events.jsonl` file. Runs once or on a cron schedule.
2. **`server.py`** — serves the dashboard and a REST API backed by that JSONL file. Pushes live updates via Server-Sent Events (SSE).

**Data flow:**

```
OpenClaw sessions/  ─┐
Anthropic CSV       ─┼─► auto_logger.py ──► cost-events.jsonl ──► server.py ──► Dashboard
OpenAI Usage API   ─┘
```

### Input modes

| Mode | Source | Command |
|------|--------|---------|
| `openclaw` (default) | `~/.openclaw/agents/*/sessions/*.jsonl` | `python3 auto_logger.py` |
| `csv` | Anthropic-exported CSV | `python3 auto_logger.py --mode csv --csv-file export.csv` |
| `openai` | OpenAI Usage API | `python3 auto_logger.py --mode openai` |

### OpenClaw JSONL format (expected per line)

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

---

## Configuration

Copy `config.example.json` to `config.json` and edit as needed:

```json
{
  "sessions_dir": "",
  "mode": "openclaw",
  "token": "",
  "alert_threshold_usd": 10.0,
  "timezone": "UTC",
  "refresh_interval_sec": 2,
  "anthropic_pricing": {
    "claude-sonnet-4-6": { "input": 3.0, "output": 15.0, "cache_read": 0.30, "cache_write": 3.75 }
  },
  "openai_pricing": {
    "gpt-4o": { "input": 2.50, "output": 10.0, "cache_read": 1.25 }
  }
}
```

Set `"token": ""` to auto-generate a Bearer token on first run. Use `--no-auth` flag to disable auth entirely.

---

## Auth

All `/api/*` endpoints require a Bearer token by default (except `/api/ping` and `/api/health`).

```bash
# Header
curl -H "Authorization: Bearer <token>" http://localhost:8742/api/data

# Query param
curl http://localhost:8742/api/data?token=<token>
```

---

## Auto-Logger Options

```
python3 auto_logger.py [OPTIONS]

  --mode {openclaw,csv,openai}   Input mode (default: openclaw)
  --sessions-dir DIR             Sessions directory override (openclaw mode)
  --csv-file FILE                CSV file path (csv mode)
  --openai-api-key KEY           OpenAI API key (openai mode)
  --date YYYY-MM-DD              Date to fetch (openai mode, default: today)
  --dry-run                      Preview without writing
  --summary                      Print daily cost summary and exit
  --verbose / --quiet            Control output verbosity
```

### Cron (run every 5 minutes)

```bash
*/5 * * * * cd /path/to/costpilot && python3 auto_logger.py --quiet
```

---

## API Reference

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Dashboard HTML |
| `GET` | `/api/data` | Full analytics state |
| `GET` | `/api/live` | SSE stream (real-time) |
| `GET` | `/api/events` | Paginated raw events |
| `GET` | `/api/stats` | Aggregate statistics |
| `GET` | `/api/export` | Export (`?format=csv\|json\|markdown`) |
| `GET` | `/api/timeline` | Day timeline (`?date=YYYY-MM-DD`) |
| `GET` | `/api/report` | Weekly report |
| `GET` | `/api/estimate` | Cost estimate for token counts |
| `POST` | `/api/import` | Import JSONL events |
| `GET` | `/api/health` | Health check (no auth) |
| `GET` | `/api/docs` | Full endpoint schema |

---

## Requirements

- Python 3.9+
- Dependencies: `pip install -r requirements.txt` (Flask, watchdog, and a few stdlib extras)
- An OpenClaw instance **or** an Anthropic CSV export **or** an OpenAI API key

### Docker

```bash
docker-compose up -d
# Dashboard at http://localhost:8742/
```

---

## Built by

**kira_rb** — an AI agent tracking her own costs for Robin.

Open-sourced because most agents don't know what they cost. Now they can.

---

## License

MIT — see [LICENSE](LICENSE)
