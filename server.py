#!/usr/bin/env python3
"""
CostPilot — HTTP Server v2.0
Real-time AI spend monitoring dashboard. Serves the frontend and API endpoints.

Architecture:
  - ThreadingHTTPServer: one thread per request (required for SSE + polling)
  - SSE broadcaster: background thread pushes delta-compressed state every N sec
  - build_state(): aggregates cost-events.jsonl into a rich analytics object
  - load_events(): file-mtime-cached loader with malformed-line skip
  - load_config(): validates fields, falls back to defaults for missing/invalid

Endpoints (50+):
  GET  /                              → dashboard.html
  GET  /api/data                      → full JSON state
  GET  /api/live                      → SSE stream
  GET  /api/events                    → paginated raw events (?page=1&page_size=50&limit=N&offset=N&from=TS&to=TS)
  GET  /api/config                    → config.json
  POST /api/config                    → save config fields
  GET  /api/health                    → {status, events, last_event_ts, uptime_sec, config_ok, events_file_writable}
  GET  /api/version                   → {version, build_date, schema_version}
  GET  /api/export                    → CSV download (rate-limited 1/5s per IP)
  GET  /api/autologger-health         → auto-logger last run age
  GET  /api/archive                   → move events >30 days to archive file
  POST /api/import                    → append JSONL events (dedup + schema validate)
  DELETE /api/clear                   → clear all events (requires token)
  GET  /api/backups                   → list backup files
  POST /api/restore                   → restore from backup
  GET  /api/stats                     → aggregate stats
  GET  /api/docs                      → endpoint schema
  GET  /api/ping                      → trivial 200 OK
  GET  /api/sessions                  → tracked session keys with labels
  GET  /api/compare                   → ?task1=X&task2=Y
  GET  /api/timeline                  → ?date=YYYY-MM-DD
  GET  /api/report                    → ?format=markdown weekly summary
  POST /api/notify                    → trigger browser notification payload
  GET  /api/annotations               → list annotations
  POST /api/annotations               → add annotation
  DELETE /api/annotations/<id>        → delete annotation
  GET  /api/estimate                  → ?model=X&input_tokens=N&output_tokens=M
  PATCH /api/events/<hash>/rename     → rename task in JSONL
"""

import argparse
import gzip
import hashlib
import io
import json
import logging
import os
import re
import shutil
import signal
import sys
import threading
import time
import uuid
from collections import defaultdict
from datetime import datetime, date, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from urllib.parse import urlparse, parse_qs
from statistics import mean, median

# ── Version ──────────────────────────────────────────────────────────────────
VERSION      = "2.0"
BUILD_DATE   = "2026-02-27"
SCHEMA_VER   = 2
START_TIME   = time.time()

# ── Paths (overridable via CLI) ───────────────────────────────────────────────
BASE_DIR       = os.path.dirname(os.path.abspath(__file__))
EVENTS_FILE    = os.path.join(BASE_DIR, "cost-events.jsonl")
DEMO_FILE      = os.path.join(BASE_DIR, "demo-data.jsonl")
DASHBOARD_FILE = os.path.join(BASE_DIR, "dashboard.html")
CONFIG_FILE    = os.path.join(BASE_DIR, "config.json")
STATE_FILE     = os.path.join(BASE_DIR, "auto-logger-state.json")
BACKUPS_DIR    = os.path.join(BASE_DIR, "backups")
ARCHIVE_FILE   = os.path.join(BASE_DIR, "cost-events-archive.jsonl")
ANNOTATIONS_FILE   = os.path.join(BASE_DIR, "annotations.json")
LAST_RUN_FILE      = os.path.join(BASE_DIR, "auto-logger-last-run.json")
GROUND_TRUTH_FILE  = os.path.join(BASE_DIR, "anthropic_ground_truth.json")
PORT               = 8742
HOST           = "0.0.0.0"
JSON_LOG       = False  # set via --json-log

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("costpilot")


def log_json(level, msg, **extra):
    """Structured JSON log line when --json-log is active."""
    if JSON_LOG:
        print(json.dumps({"ts": time.time(), "level": level, "msg": msg, **extra}), flush=True)
    else:
        getattr(logger, level)(msg)


# ── Config ────────────────────────────────────────────────────────────────────
CONFIG_DEFAULTS = {
    "user": "User",
    "project": "AI Operations",
    "currency": "USD",
    "currency_rate": 1.0,
    "timezone": "UTC",
    "alert_threshold_usd": 10.0,   # per-task colour threshold
    "daily_budget_usd": 200.0,     # daily spend cap (separate from per-task)
    "alert_levels": {"warn": 150.0, "critical": 200.0},  # daily alert levels
    "refresh_interval_sec": 2,
    "theme": "dark",
    "date_format": "relative",
    "default_sort": "ts",
    "default_filter": "ALL",
    "show_sessions": True,
    "compact_default": False,
    "max_events_display": 50,
    "hide_zero_cost": False,
    "group_by_task": False,
    "show_token_counts": True,
    "cost_precision": 4,
    "dashboard_title": "CostPilot",
    "weekly_goal_usd": 0.0,
    "model_aliases": {},
    "session_label_overrides": {},
    "exclude_sessions": [],
    "webhook_url": "",
    "notify_on_threshold": False,
    "retention_days": 90,
    "basic_auth": {},
    "categories": [
        {"keyword": "MALL",  "label": "MALL",  "color": "yellow"},
        {"keyword": "MOLT",  "label": "MOLT",  "color": "cyan"},
        {"keyword": "CCK",   "label": "CCK",   "color": "green"},
        {"keyword": "ARENA", "label": "ARENA", "color": "gold"},
        {"keyword": "NEWS",  "label": "NEWS",  "color": "blue"},
        {"keyword": "OPS",   "label": "OPS",   "color": "red"},
        {"keyword": "KIRA",  "label": "KIRA",  "color": "blue"},
    ],
}

CONFIG_FIELD_TYPES = {
    "user": str, "project": str, "currency": str, "timezone": str,
    "alert_threshold_usd": (int, float), "daily_budget_usd": (int, float), "refresh_interval_sec": (int, float),
    "theme": str, "date_format": str, "default_sort": str, "default_filter": str,
    "show_sessions": bool, "compact_default": bool, "hide_zero_cost": bool,
    "group_by_task": bool, "show_token_counts": bool,
    "max_events_display": int, "cost_precision": int, "retention_days": int,
    "weekly_goal_usd": (int, float), "currency_rate": (int, float),
    "notify_on_threshold": bool, "webhook_url": str,
}

_config_cache = None
_config_mtime = 0.0


def load_config(force=False):
    """Load + validate config.json. Falls back to defaults for missing/invalid fields."""
    global _config_cache, _config_mtime
    try:
        mtime = os.path.getmtime(CONFIG_FILE) if os.path.exists(CONFIG_FILE) else 0
    except OSError:
        mtime = 0
    if not force and _config_cache is not None and mtime == _config_mtime:
        return _config_cache

    cfg = dict(CONFIG_DEFAULTS)
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE) as f:
                raw = json.load(f)
            # Validate known fields
            for k, v in raw.items():
                expected = CONFIG_FIELD_TYPES.get(k)
                if expected is None:
                    cfg[k] = v  # Unknown fields pass through
                elif isinstance(expected, tuple):
                    if isinstance(v, expected):
                        cfg[k] = v
                    else:
                        log_json("warning", f"Config field '{k}' has wrong type, using default")
                else:
                    if isinstance(v, expected):
                        cfg[k] = v
                    else:
                        log_json("warning", f"Config field '{k}' has wrong type, using default")
        except Exception as e:
            log_json("warning", f"Config load error: {e}, using defaults")

    _config_cache = cfg
    _config_mtime = mtime
    return cfg


# ── Ground truth (Anthropic CSV import) ──────────────────────────────────────
_gt_cache  = None
_gt_mtime  = 0.0
_gt_lock   = threading.Lock()

def load_ground_truth(force=False):
    """Load anthropic_ground_truth.json with mtime caching."""
    global _gt_cache, _gt_mtime
    with _gt_lock:
        try:
            mtime = os.path.getmtime(GROUND_TRUTH_FILE) if os.path.exists(GROUND_TRUTH_FILE) else 0
        except OSError:
            mtime = 0
        if not force and _gt_cache is not None and mtime == _gt_mtime:
            return _gt_cache
        if not os.path.exists(GROUND_TRUTH_FILE):
            _gt_cache = {}
            _gt_mtime = mtime
            return {}
        try:
            with open(GROUND_TRUTH_FILE) as f:
                data = json.load(f)
            _gt_cache = data
            _gt_mtime = mtime
            return data
        except Exception as e:
            log_json("warning", f"Ground truth load error: {e}")
            _gt_cache = {}
            return {}


# ── File locking ──────────────────────────────────────────────────────────────
_events_write_lock = threading.Lock()


def write_events_locked(new_lines):
    """Thread-safe append to events file."""
    with _events_write_lock:
        with open(EVENTS_FILE, "a") as f:
            for line in new_lines:
                f.write(json.dumps(line) + "\n")


# ── Event loading (mtime-cached) ──────────────────────────────────────────────
_events_cache     = None
_events_mtime     = 0.0
_events_demo_mode = False
_events_lock      = threading.Lock()
_malformed_count  = 0


_SESSION_LABEL_CACHE = {}  # session_id → enriched label

def _load_spawn_labels():
    """Read sessions.json and return {session_uuid: spawn_label} for sub-agents."""
    import json as _json
    sfile = os.path.join(os.path.expanduser("~"), ".openclaw", "agents", "main", "sessions", "sessions.json")
    result = {}
    try:
        with open(sfile) as f:
            data = _json.load(f)
        for key, val in data.items():
            if isinstance(val, dict):
                sid = val.get("sessionId")
                lbl = val.get("label")
                if sid and lbl:
                    result[sid] = lbl
    except Exception:
        pass
    return result


def _enrich_session_labels(events):
    """
    Post-process events to replace anonymous labels with meaningful ones.
    Priority:
      1. sessions.json spawn label (e.g. "costpilot-rename-fix")
      2. Model + timestamp ("Sonnet · Feb 27 04:00") for "Session XXXXXXXX" patterns
    Mutates events in-place.
    """
    import re as _re
    _SESSION_RE = _re.compile(r'^Session [0-9a-f]{8}$')
    _MODEL_SHORT = {"sonnet": "Sonnet", "opus": "Opus", "haiku": "Haiku"}
    _MONTHS = ["Jan","Feb","Mar","Apr","May","Jun",
               "Jul","Aug","Sep","Oct","Nov","Dec"]

    # Build uuid → spawn label map from sessions.json
    spawn_labels = _load_spawn_labels()

    # Pass 1: apply spawn labels (by session UUID field)
    for ev in events:
        uuid = ev.get("session", "")
        if uuid and uuid in spawn_labels:
            ev["task"] = spawn_labels[uuid]

    # Pass 2: enrich remaining "Session XXXXXXXX" with model+timestamp
    anon_groups = {}
    for i, ev in enumerate(events):
        task = ev.get("task", "")
        if _SESSION_RE.match(task):
            anon_groups.setdefault(task, []).append(i)

    for label, indices in anon_groups.items():
        if label in _SESSION_LABEL_CACHE:
            new_label = _SESSION_LABEL_CACHE[label]
        else:
            first_ev = min((events[i] for i in indices), key=lambda e: e.get("ts", 0))
            model_raw  = (first_ev.get("model") or "").lower()
            model_short = next((v for k, v in _MODEL_SHORT.items() if k in model_raw), "AI")
            ts = first_ev.get("ts", 0)
            if ts:
                dt = datetime.fromtimestamp(ts)
                new_label = f"{model_short} · {_MONTHS[dt.month-1]} {dt.day} {dt.hour:02d}:00"
            else:
                new_label = label
            _SESSION_LABEL_CACHE[label] = new_label

        if new_label != label:
            for i in indices:
                events[i]["task"] = new_label


def load_events(force=False):
    """Load events from file with mtime caching and malformed-line resilience."""
    global _events_cache, _events_mtime, _events_demo_mode, _malformed_count
    with _events_lock:
        try:
            mtime = os.path.getmtime(EVENTS_FILE) if os.path.exists(EVENTS_FILE) else 0
        except OSError:
            mtime = 0

        if not force and _events_cache is not None and mtime == _events_mtime:
            return _events_cache, _events_demo_mode

        demo_mode = False
        events    = []
        bad_lines = 0

        fpath = EVENTS_FILE if os.path.exists(EVENTS_FILE) else None

        if fpath:
            with open(fpath) as f:
                for lineno, line in enumerate(f, 1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        ev = json.loads(line)
                        # Add stable id if missing
                        if "id" not in ev:
                            raw = f"{ev.get('ts','')}{ev.get('task','')}{ev.get('cost_usd','')}"
                            ev["id"] = hashlib.md5(raw.encode()).hexdigest()[:12]
                        events.append(ev)
                    except json.JSONDecodeError:
                        bad_lines += 1

        if bad_lines > 0:
            log_json("warning", f"Skipped {bad_lines} malformed lines in events file")
            _malformed_count = bad_lines

        # Fallback to demo data
        if not events and os.path.exists(DEMO_FILE):
            demo_mode = True
            with open(DEMO_FILE) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            events.append(json.loads(line))
                        except json.JSONDecodeError:
                            pass

        # Enrich anonymous "Session XXXXXXXX" labels with model+timestamp
        if not demo_mode:
            _enrich_session_labels(events)

        _events_cache     = events
        _events_mtime     = mtime
        _events_demo_mode = demo_mode
        return events, demo_mode


def event_id(ev):
    """Stable hash for an event."""
    raw = f"{ev.get('ts','')}{ev.get('task','')}{ev.get('cost_usd','')}"
    return hashlib.md5(raw.encode()).hexdigest()[:12]


# ── SSE state ─────────────────────────────────────────────────────────────────
_sse_clients  = []
_sse_lock     = threading.Lock()
_last_sse_state = None  # For delta compression


# ── Analytics helpers ─────────────────────────────────────────────────────────

def percentile(data, pct):
    if not data:
        return 0
    s = sorted(data)
    k = (len(s) - 1) * pct / 100
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def linear_regression(xs, ys):
    """Simple least-squares linear regression. Returns (slope, intercept)."""
    n = len(xs)
    if n < 2:
        return 0, ys[0] if ys else 0
    sx  = sum(xs)
    sy  = sum(ys)
    sxy = sum(x * y for x, y in zip(xs, ys))
    sxx = sum(x * x for x in xs)
    denom = n * sxx - sx * sx
    if denom == 0:
        return 0, sy / n
    slope     = (n * sxy - sx * sy) / denom
    intercept = (sy - slope * sx) / n
    return slope, intercept


def parse_tags(task_name):
    """Extract [tag] patterns from task name."""
    return re.findall(r'\[([^\]]+)\]', task_name or '')


def _short_day(iso_date: str) -> str:
    d = date.fromisoformat(iso_date)
    days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    return days[d.weekday()]


# ── build_state ───────────────────────────────────────────────────────────────
_state_cache      = None
_state_cache_ts   = 0.0
_state_cache_lock = threading.Lock()

def build_state():
    """
    Aggregate cost-events.jsonl into a rich analytics object.
    Cached for 1 second to avoid recomputing on rapid requests.
    Logs a warning if computation takes >500ms.
    """
    global _state_cache, _state_cache_ts
    with _state_cache_lock:
        now = time.time()
        if _state_cache is not None and (now - _state_cache_ts) < 1.0:
            return _state_cache

        t0 = time.perf_counter()
        result = _build_state_inner()
        elapsed_ms = (time.perf_counter() - t0) * 1000
        if elapsed_ms > 500:
            log_json("warning", f"build_state() took {elapsed_ms:.0f}ms — consider archiving old events")

        _state_cache    = result
        _state_cache_ts = now
        return result


def _build_ground_truth_section(gt, rate, tracked_today, tracked_week, tracked_month, today_start, week_start, month_start, daily_tracked=None):
    """Build the ground_truth section for the API response."""
    if not gt:
        return {"available": False}

    today_iso = date.fromtimestamp(today_start).isoformat()
    daily     = gt.get("daily", {})

    # Today's real cost
    gt_today  = daily.get(today_iso, {}).get("cost_usd", None)

    # Week (last 7 days)
    gt_week   = 0.0
    for i in range(7):
        d = (date.today() - timedelta(days=i)).isoformat()
        gt_week += daily.get(d, {}).get("cost_usd", 0.0)

    # Month (current calendar month)
    gt_month  = 0.0
    month_start_d = date.fromtimestamp(month_start).isoformat()[:7]  # "YYYY-MM"
    for d, v in daily.items():
        if d.startswith(month_start_d):
            gt_month += v.get("cost_usd", 0.0)

    # Daily list for chart (last 7 days)
    daily_list = []
    for i in range(6, -1, -1):
        d = (date.today() - timedelta(days=i)).isoformat()
        dv = daily.get(d, {})
        tracked = round(daily_tracked.get(d, 0.0) * rate, 2) if daily_tracked else None
        daily_list.append({
            "date":         d,
            "real_cost":    round(dv.get("cost_usd", 0.0) * rate, 2),
            "tracked_cost": tracked,
            "models":       dv.get("models", []),
        })

    # Fill tracked_cost per day from events
    # NOTE: events are tracked locally, gt is from Anthropic. We compare them.

    # All available dates in gt
    all_dates_list = sorted(daily.keys())
    full_daily_gt  = [
        {
            "date":       d,
            "real_cost":  round(v.get("cost_usd", 0.0) * rate, 2),
            "cache_w_5m": v.get("cache_w_5m", 0),
            "cache_read": v.get("cache_read", 0),
            "output":     v.get("output", 0),
            "models":     v.get("models", []),
        }
        for d, v in sorted(daily.items())
    ]

    # Aggregate cache/write tokens across all GT days
    total_cache_read_gt  = sum(v.get("cache_read", 0)  for v in daily.values())
    total_cache_write_gt = sum(v.get("cache_w_5m", 0) for v in daily.values())
    total_output_gt      = sum(v.get("output", 0)     for v in daily.values())

    # Accuracy: compare tracked vs real for today
    accuracy_today = None
    if gt_today and gt_today > 0 and tracked_today > 0:
        accuracy_today = round(tracked_today * rate / (gt_today * rate) * 100, 1)

    # Average daily cost from GT (all available days)
    gt_all_total = sum(v.get("cost_usd", 0.0) for v in daily.values())
    gt_avg_daily = round(gt_all_total / len(daily) * rate, 2) if daily else None

    return {
        "available":       True,
        "generated_at":    gt.get("generated_at", ""),
        "source_files":    gt.get("source_files", []),
        "today_real_cost": round(gt_today * rate, 2) if gt_today is not None else None,
        "week_real_cost":  round(gt_week * rate, 2),
        "month_real_cost": round(gt_month * rate, 2),
        "total_real_cost": round(gt_all_total * rate, 2),
        "avg_daily_real":  gt_avg_daily,
        "today_tracked_cost": round(tracked_today * rate, 2),
        "week_tracked_cost":  round(tracked_week * rate, 2),
        "month_tracked_cost": round(tracked_month * rate, 2),
        "accuracy_today_pct": accuracy_today,
        "cache_read_total":  total_cache_read_gt,
        "cache_write_total": total_cache_write_gt,
        "output_tokens_total": total_output_gt,
        "daily_list":      daily_list,
        "full_daily":      full_daily_gt,
        "gt_hourly_today": gt.get("hourly", {}).get(today_iso, []),
        "gt_hourly_all":   gt.get("hourly", {}),
        "cache_fix_date":  gt.get("cache_fix_date"),
        "cache_fix_savings_pct": gt.get("cache_fix_savings_pct"),
        "avg_daily_pre_fix": gt.get("avg_daily_pre_fix"),
        "feb24_projected": gt.get("feb24_projected_full_day"),
    }


def _build_state_inner():
    """Inner implementation of build_state (not cached)."""
    cfg    = load_config()
    events, demo_mode = load_events()
    gt     = load_ground_truth()
    now    = time.time()

    # Apply hide_zero_cost filter
    if cfg.get("hide_zero_cost"):
        events = [e for e in events if e.get("cost_usd", 0) > 0]

    today_start  = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    week_start   = today_start - 6 * 86400
    month_start  = datetime.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp()
    yesterday_start = today_start - 86400

    today_events     = [e for e in events if e.get("ts", 0) >= today_start]
    week_events      = [e for e in events if e.get("ts", 0) >= week_start]
    month_events     = [e for e in events if e.get("ts", 0) >= month_start]
    yesterday_events = [e for e in events if yesterday_start <= e.get("ts", 0) < today_start]

    tracked_today_cost    = sum(e.get("cost_usd", 0) for e in today_events)
    tracked_week_cost     = sum(e.get("cost_usd", 0) for e in week_events)
    tracked_month_cost    = sum(e.get("cost_usd", 0) for e in month_events)
    tracked_yesterday_cost = sum(e.get("cost_usd", 0) for e in yesterday_events)

    # Use GT real cost for today/week/month when available
    _gt_early = load_ground_truth()
    _gt_daily_early = _gt_early.get("daily", {}) if _gt_early else {}
    today_iso_early = date.fromtimestamp(today_start).isoformat()

    if today_iso_early in _gt_daily_early:
        today_cost = _gt_daily_early[today_iso_early].get("cost_usd", tracked_today_cost)
    else:
        today_cost = tracked_today_cost

    # Week/month: sum GT where available, fall back to tracked for missing days
    week_cost  = sum(_gt_daily_early.get((date.fromtimestamp(week_start + i*86400)).isoformat(), {}).get("cost_usd", 0)
                     for i in range(7)) or tracked_week_cost
    month_cost = sum(v.get("cost_usd", 0) for d, v in _gt_daily_early.items()
                     if d.startswith(date.fromtimestamp(today_start).isoformat()[:7])) or tracked_month_cost

    # Yesterday: use GT when available
    yesterday_iso = date.fromtimestamp(yesterday_start).isoformat()
    if yesterday_iso in _gt_daily_early:
        yesterday_cost = _gt_daily_early[yesterday_iso].get("cost_usd", tracked_yesterday_cost)
    else:
        yesterday_cost = tracked_yesterday_cost

    # GT avg daily real (for dashboard comparisons — use GT avg when available, else tracked 30d avg)
    _gt_daily_values = [v.get("cost_usd", 0) for v in _gt_daily_early.values() if v.get("cost_usd", 0) > 0]
    gt_avg_daily_real = (sum(_gt_daily_values) / len(_gt_daily_values)) if _gt_daily_values else None

    # Running tasks
    running      = [e for e in events if e.get("status") == "running"]
    running_cost = sum(e.get("cost_usd", 0) for e in running)

    completed_today = [e for e in today_events if e.get("status") == "completed"]
    avg_task_cost   = (sum(e.get("cost_usd", 0) for e in completed_today) / len(completed_today)
                       if completed_today else 0)

    elapsed_frac = (now - today_start) / 86400
    # today_cost is now GT real cost when available → project from it directly
    projection   = (today_cost / elapsed_frac) if elapsed_frac > 0.01 else 0
    forecast_source = "ground_truth" if today_iso_early in _gt_daily_early else "tracking"

    # Apply currency conversion
    rate = float(cfg.get("currency_rate", 1.0) or 1.0)

    def conv(v):
        return round(v * rate, 6)

    # ── Model label helper ─────────────────────────────────────────────────────
    def _model_label(m):
        m = m.lower()
        if "opus" in m:        return "Claude Opus"
        if "sonnet" in m:      return "Claude Sonnet"
        if "haiku" in m:       return "Claude Haiku"
        if "gpt-4o-mini" in m: return "GPT-4o mini"
        if "gpt-4o" in m:      return "GPT-4o"
        if "gpt-4" in m:       return "GPT-4"
        if "gpt-3" in m:       return "GPT-3.5"
        if "gemini" in m:      return "Gemini"
        if "mistral" in m:     return "Mistral"
        return m[:20]

    # ── Model breakdown ────────────────────────────────────────────────────────
    def calc_model_breakdown(event_list):
        model_totals = defaultdict(lambda: {"cost": 0.0, "tokens_in": 0, "tokens_out": 0, "tokens_cache": 0, "runs": 0})
        for e in event_list:
            model = e.get("model", "unknown") or "unknown"
            model_totals[model]["cost"]         += e.get("cost_usd", 0)
            model_totals[model]["tokens_in"]    += e.get("input_tokens", 0)
            model_totals[model]["tokens_out"]   += e.get("output_tokens", 0)
            model_totals[model]["tokens_cache"] += e.get("cache_read_tokens", 0)
            model_totals[model]["runs"]         += 1
        total_cost = sum(v["cost"] for v in model_totals.values()) or 1
        return sorted(
            [{"model": m, "label": _model_label(m),
              "cost":      round(v["cost"] * rate, 4),
              "pct":       round(v["cost"] / total_cost * 100, 1),
              "runs":      v["runs"],
              "tokens_in":    v["tokens_in"],
              "tokens_out":   v["tokens_out"],
              "tokens_cache": v["tokens_cache"]}
             for m, v in model_totals.items()],
            key=lambda x: -x["cost"]
        )

    # ── Task Breakdown ──────────────────────────────────────────────────────────
    def calc_breakdown(event_list):
        session_totals = defaultdict(lambda: {"cost": 0.0, "runs": 0})
        for e in event_list:
            # Use enriched task label (smart label) instead of raw session UUID
            s = e.get("task") or e.get("session", "other")
            session_totals[s]["cost"] += e.get("cost_usd", 0)
            session_totals[s]["runs"] += 1
        return sorted(
            [{"session": s, "cost": round(v["cost"] * rate, 4), "runs": v["runs"]}
             for s, v in session_totals.items()],
            key=lambda x: -x["cost"]
        )

    breakdown       = calc_breakdown(today_events)
    breakdown_week  = calc_breakdown(week_events)
    breakdown_month = calc_breakdown(month_events)

    breakdown_by_model       = calc_model_breakdown(today_events)
    breakdown_by_model_week  = calc_model_breakdown(week_events)
    breakdown_by_model_month = calc_model_breakdown(month_events)

    # ── Weekly chart (last 7 days) ─────────────────────────────────────────────
    daily_cost    = {}
    daily_sessions = {}
    for i in range(7):
        d = (date.today() - timedelta(days=6 - i)).isoformat()
        daily_cost[d]     = 0.0
        daily_sessions[d] = set()

    for e in week_events:
        d = date.fromtimestamp(e.get("ts", 0)).isoformat()
        if d in daily_cost:
            daily_cost[d] += e.get("cost_usd", 0)
            daily_sessions[d].add(e.get("session", "other"))

    weekly_chart = [
        {"date": d, "cost": round(c * rate, 4), "label": _short_day(d),
         "session_count": len(daily_sessions[d]),
         "tracked_cost": round(c * rate, 4)}
        for d, c in daily_cost.items()
    ]
    # Override weekly_chart costs with GT real costs where available
    _gt_daily_wk = _gt_daily_early  # already loaded above
    for row in weekly_chart:
        if row["date"] in _gt_daily_wk:
            row["cost"]        = round(_gt_daily_wk[row["date"]].get("cost_usd", row["cost"]) * rate, 2)
            row["is_gt"]       = True
        else:
            row["is_gt"]       = False

    # 30-day rolling average for chart annotation
    thirty_days_ago = today_start - 29 * 86400
    events_30d = [e for e in events if e.get("ts", 0) >= thirty_days_ago]
    daily_30d  = defaultdict(float)
    for e in events_30d:
        d = date.fromtimestamp(e.get("ts", 0)).isoformat()
        daily_30d[d] += e.get("cost_usd", 0)
    avg_30d = (sum(daily_30d.values()) / max(len(daily_30d), 1)) * rate

    # 3-day forecast via linear regression
    x_vals = list(range(7))
    y_vals = [w["cost"] for w in weekly_chart]
    slope, intercept = linear_regression(x_vals, y_vals)
    forecast_3d = [
        {"day": i + 1, "cost": max(0, round(slope * (7 + i) + intercept, 4))}
        for i in range(3)
    ]

    # Week-over-week comparison
    last_week_start = week_start - 7 * 86400
    last_week_events = [e for e in events if last_week_start <= e.get("ts", 0) < week_start]
    last_week_cost   = sum(e.get("cost_usd", 0) for e in last_week_events) * rate
    wow_pct = ((week_cost * rate - last_week_cost) / last_week_cost * 100) if last_week_cost > 0 else 0

    # ── Recent tasks (paginated) ───────────────────────────────────────────────
    precision = cfg.get("cost_precision", 4)
    max_events = cfg.get("max_events_display", 50)
    # Tag extraction
    aliases = cfg.get("model_aliases", {})

    recent = sorted(events, key=lambda e: e.get("ts", 0), reverse=True)[:max_events]

    # Recurring tasks: tasks appearing ≥3 times
    task_counts = defaultdict(int)
    for e in events:
        t = (e.get("task") or "").strip()
        if t:
            task_counts[t] += 1
    recurring_tasks = {t for t, n in task_counts.items() if n >= 3}

    # Per-task averages for anomaly detection
    task_cost_lists = defaultdict(list)
    for e in events:
        t = (e.get("task") or "").strip()
        if t and e.get("cost_usd", 0) > 0:
            task_cost_lists[t].append(e.get("cost_usd", 0))
    task_avg = {t: mean(cs) for t, cs in task_cost_lists.items() if cs}

    recent_tasks = []
    for e in recent:
        age_sec = now - e.get("ts", 0)
        task    = e.get("task", "Unknown")
        cost    = e.get("cost_usd", 0)
        model   = e.get("model", "")
        tags    = parse_tags(task)
        is_recurring = task in recurring_tasks
        avg_cost_for_task = task_avg.get(task, 0)
        is_anomaly = avg_cost_for_task > 0 and cost > 5 * avg_cost_for_task and cost > 0.5
        model_display = aliases.get(model, model)

        recent_tasks.append({
            "ts":                e.get("ts", 0),
            "id":                e.get("id", event_id(e)),
            "task":              task,
            "model":             model,
            "model_display":     model_display,
            "cost":              round(cost * rate, precision),
            "status":            e.get("status", ""),
            "duration_sec":      e.get("duration_sec", 0),
            "session":           e.get("session", ""),
            "age_sec":           round(age_sec),
            "anomaly":           e.get("anomaly") or (is_anomaly and cost),
            "input_tokens":      e.get("input_tokens", 0),
            "output_tokens":     e.get("output_tokens", 0),
            "cache_read_tokens": e.get("cache_read_tokens", 0),
            "tags":              tags,
            "is_recurring":      is_recurring,
        })

    # ── Token stats ────────────────────────────────────────────────────────────
    total_input_today  = sum(e.get("input_tokens", 0)  for e in today_events)
    total_output_today = sum(e.get("output_tokens", 0) for e in today_events)
    total_cache_today  = sum(e.get("cache_read_tokens", 0) for e in today_events)

    # Token ratio
    if total_input_today + total_output_today > 0:
        token_ratio = round(total_input_today / max(total_output_today, 1), 1)
    else:
        token_ratio = 0

    # Cache savings (assuming cache_read costs 10% of fresh input)
    SONNET_INPUT_PER_M = 3.0
    cache_savings = (total_cache_today / 1_000_000) * SONNET_INPUT_PER_M * 0.9 * rate

    # ── Status lights ──────────────────────────────────────────────────────────
    threshold     = float(cfg.get("alert_threshold_usd", 10.0) or 10.0) * rate  # per-task
    daily_budget  = float(cfg.get("daily_budget_usd", cfg.get("alert_threshold_usd", 200.0)) or 200.0) * rate  # daily cap

    def day_status(cost):
        # Use daily_budget (not per-task threshold) for day-level status
        if cost < daily_budget * 0.3:  return "green"
        if cost < daily_budget:        return "yellow"
        return "red"

    def avg_status(avg):
        if avg < 0.30 * rate: return "green"
        if avg < 1.00 * rate: return "yellow"
        return "red"

    def proj_status(proj):
        if proj < daily_budget * 0.3: return "green"
        if proj < daily_budget:       return "yellow"
        return "red"

    # Alert level
    alert_levels = cfg.get("alert_levels", {})
    _db_usd      = float(cfg.get("daily_budget_usd", 200.0) or 200.0)
    warn_thresh  = float(alert_levels.get("warn",     _db_usd * 0.75) or (_db_usd * 0.75)) * rate
    crit_thresh  = float(alert_levels.get("critical", _db_usd)        or _db_usd)          * rate
    if today_cost * rate >= crit_thresh:
        alert_level = "critical"
    elif today_cost * rate >= warn_thresh:
        alert_level = "warn"
    else:
        alert_level = "ok"

    # ── Hourly heatmap ─────────────────────────────────────────────────────────
    hourly = [0.0] * 24
    hourly_by_day = defaultdict(lambda: [0.0] * 24)  # iso_date → [24 hours]
    for e in events:
        h = datetime.fromtimestamp(e.get("ts", 0)).hour
        hourly[h] += e.get("cost_usd", 0)
    for e in week_events:
        d = date.fromtimestamp(e.get("ts", 0)).isoformat()
        h = datetime.fromtimestamp(e.get("ts", 0)).hour
        hourly_by_day[d][h] += e.get("cost_usd", 0)
    hourly_costs = [round(v * rate, 4) for v in hourly]

    # 7-day hourly average
    hourly_7d_avg = []
    for h in range(24):
        day_vals = [hourly_by_day[d][h] for d in hourly_by_day if hourly_by_day[d][h] > 0]
        hourly_7d_avg.append(round(mean(day_vals) * rate if day_vals else 0, 4))

    # breakdown_by_hour
    breakdown_by_hour = [{"hour": h, "cost": hourly_costs[h]} for h in range(24)]

    # ── Model split ────────────────────────────────────────────────────────────
    model_split_raw = defaultdict(float)
    for e in today_events:
        m = (e.get("model") or "").lower()
        if "sonnet" in m:
            model_split_raw["sonnet"] += e.get("cost_usd", 0)
        elif "opus" in m:
            model_split_raw["opus"] += e.get("cost_usd", 0)
        elif "haiku" in m:
            model_split_raw["haiku"] += e.get("cost_usd", 0)
        else:
            model_split_raw["other"] += e.get("cost_usd", 0)
    model_split = {k: round(v * rate, 4) for k, v in model_split_raw.items()}

    # ── Trend ─────────────────────────────────────────────────────────────────
    three_days_ago = today_start - 3 * 86400
    week_completed = [e for e in week_events if e.get("status") == "completed"]
    recent_3d  = [e for e in week_completed if e.get("ts", 0) >= three_days_ago]
    prior_4d   = [e for e in week_completed if e.get("ts", 0) < three_days_ago]
    avg_recent = (sum(e.get("cost_usd", 0) for e in recent_3d) / len(recent_3d)) if recent_3d else 0
    avg_prior  = (sum(e.get("cost_usd", 0) for e in prior_4d) / len(prior_4d)) if prior_4d else 0
    trend_pct  = ((avg_recent - avg_prior) / avg_prior * 100) if avg_prior > 0 else 0

    # Efficiency trend (7-day eff% direction)
    week_eff_vals = []
    for e in week_completed:
        inp = e.get("input_tokens", 0)
        out = e.get("output_tokens", 0)
        if inp + out > 0:
            week_eff_vals.append(out / (inp + out))
    eff_trend = "flat"
    if len(week_eff_vals) >= 4:
        mid = len(week_eff_vals) // 2
        early = mean(week_eff_vals[:mid])
        late  = mean(week_eff_vals[mid:])
        if late > early * 1.05:
            eff_trend = "improving"
        elif late < early * 0.95:
            eff_trend = "declining"

    # ── Peak task ─────────────────────────────────────────────────────────────
    peak_task = None
    if completed_today:
        pt = max(completed_today, key=lambda e: e.get("cost_usd", 0))
        peak_task = {"task": pt.get("task", "Unknown"), "cost": round(pt.get("cost_usd", 0) * rate, precision), "id": pt.get("id", event_id(pt))}

    # All-time peak
    all_events = [e for e in events if e.get("status") == "completed"]
    peak_task_all_time = None
    if all_events:
        pt_all = max(all_events, key=lambda e: e.get("cost_usd", 0))
        peak_task_all_time = {
            "task": pt_all.get("task", "Unknown"),
            "cost": round(pt_all.get("cost_usd", 0) * rate, precision),
            "date": datetime.fromtimestamp(pt_all.get("ts", 0)).strftime("%Y-%m-%d"),
        }

    # Longest session
    dur_events = [e for e in events if e.get("duration_sec", 0) > 0]
    longest_session = None
    if dur_events:
        ls = max(dur_events, key=lambda e: e.get("duration_sec", 0))
        longest_session = {
            "task": ls.get("task", "Unknown"),
            "duration_sec": ls.get("duration_sec", 0),
            "date": datetime.fromtimestamp(ls.get("ts", 0)).strftime("%Y-%m-%d"),
        }

    # ── Analytics: recurring, anomalies, tags, leaderboard ───────────────────
    # Task leaderboard (sorted by avg eff%)
    task_eff = {}
    task_stats_map = defaultdict(lambda: {"costs": [], "durations": [], "inp": 0, "out": 0})
    for e in events:
        t = (e.get("task") or "Unknown").strip()
        task_stats_map[t]["costs"].append(e.get("cost_usd", 0))
        task_stats_map[t]["durations"].append(e.get("duration_sec", 0))
        task_stats_map[t]["inp"] += e.get("input_tokens", 0)
        task_stats_map[t]["out"] += e.get("output_tokens", 0)

    task_leaderboard = []
    for t, s in task_stats_map.items():
        total = s["inp"] + s["out"]
        eff_pct = round(s["out"] / total * 100, 1) if total > 0 else 0
        avg_c   = round(mean(s["costs"]) * rate, precision) if s["costs"] else 0
        task_leaderboard.append({
            "task": t, "eff_pct": eff_pct,
            "avg_cost": avg_c,
            "runs": len(s["costs"]),
            "p90_cost": round(percentile(s["costs"], 90) * rate, precision),
        })
    task_leaderboard.sort(key=lambda x: -x["eff_pct"])

    # Percentile stats (all events)
    all_costs = [e.get("cost_usd", 0) for e in events if e.get("cost_usd", 0) > 0]
    percentile_stats = {
        "p50": round(percentile(all_costs, 50) * rate, precision),
        "p90": round(percentile(all_costs, 90) * rate, precision),
        "p99": round(percentile(all_costs, 99) * rate, precision),
    } if all_costs else {"p50": 0, "p90": 0, "p99": 0}

    # Task frequency (runs per day avg)
    date_range_days = max(1, len(daily_cost))
    task_frequency = {
        t: round(n / date_range_days, 2)
        for t, n in task_counts.items()
    }
    top_frequent = sorted(task_frequency.items(), key=lambda x: -x[1])[:3]

    # Tags summary
    tags_summary = defaultdict(float)
    for e in events:
        for tag in parse_tags(e.get("task", "")):
            tags_summary[tag] += e.get("cost_usd", 0) * rate
    tags_summary_list = sorted(
        [{"tag": t, "cost": round(c, precision)} for t, c in tags_summary.items()],
        key=lambda x: -x["cost"]
    )

    # Busiest day of week
    weekday_costs = defaultdict(list)
    for e in events:
        wd = datetime.fromtimestamp(e.get("ts", 0)).strftime("%A")
        weekday_costs[wd].append(e.get("cost_usd", 0))
    cost_by_weekday = {
        wd: round(mean(vals) * rate, precision)
        for wd, vals in weekday_costs.items() if vals
    }
    busiest_day = max(cost_by_weekday.items(), key=lambda x: x[1])[0] if cost_by_weekday else None

    # Input:output ratio per model
    model_io_ratio = defaultdict(lambda: {"inp": 0, "out": 0})
    for e in events:
        m = (e.get("model") or "unknown").lower()
        model_io_ratio[m]["inp"] += e.get("input_tokens", 0)
        model_io_ratio[m]["out"] += e.get("output_tokens", 0)
    io_ratios = {
        m: round(v["inp"] / max(v["out"], 1), 1)
        for m, v in model_io_ratio.items()
    }

    # Budget
    daily_budget_remaining = max(0, daily_budget - today_cost * rate)
    projected_month_cost   = round(today_cost * 30 * rate, 2)

    # Data volume warning
    events_today_count = len(today_events)
    data_volume_warning = events_today_count > 1000

    # Session count per day
    session_count_today = len({e.get("session", "") for e in today_events})

    # Cost per hour (last 7 days)
    cost_per_hour_7d = defaultdict(float)
    for e in week_events:
        h = datetime.fromtimestamp(e.get("ts", 0)).hour
        cost_per_hour_7d[h] += e.get("cost_usd", 0) * rate
    cost_per_hour_7d = [round(cost_per_hour_7d.get(h, 0), 4) for h in range(24)]

    # All-time totals
    total_cost_tracked    = round(sum(e.get("cost_usd", 0) for e in events) * rate, precision)
    total_events_all_time = len(events)

    # Use Ground Truth total when available (vastly more accurate)
    _gt_all = load_ground_truth()
    if _gt_all and _gt_all.get("daily"):
        _gt_sum = sum(v.get("cost_usd", 0) for v in _gt_all["daily"].values())
        total_cost_all_time = round(_gt_sum * rate, 2)
    else:
        total_cost_all_time = total_cost_tracked

    # Anomalies (today)
    anomalies_today = [e for e in today_events if e.get("anomaly")]
    # Also flag computed anomalies
    computed_anomalies = []
    for e in today_events:
        task = (e.get("task") or "").strip()
        avg_c = task_avg.get(task, 0)
        if avg_c > 0 and e.get("cost_usd", 0) > 5 * avg_c and e.get("cost_usd", 0) > 0.5:
            computed_anomalies.append(e)
    all_anomalies = {e.get("id", ""): e for e in anomalies_today + computed_anomalies}
    anomaly_list = [
        {"task": e.get("task", "Unknown"), "note": e.get("anomaly", "Cost spike"),
         "cost": round(e.get("cost_usd", 0) * rate, precision)}
        for e in all_anomalies.values()
    ]

    # Cost velocity (running tasks tokens/min estimate)
    cost_velocity = 0
    if running:
        for r in running:
            dur = max(r.get("duration_sec", 1), 1)
            cost_velocity += r.get("cost_usd", 0) / dur * 60

    # Weekly goal progress
    weekly_goal = float(cfg.get("weekly_goal_usd", 0.0) or 0)
    weekly_goal_pct = round(min(100, week_cost * rate / weekly_goal * 100), 1) if weekly_goal > 0 else 0

    return {
        "ts":            int(now),
        "demo_mode":     demo_mode,
        "schema_version": SCHEMA_VER,
        "currency":      cfg.get("currency", "USD"),
        "currency_rate": rate,
        "config": {
            "theme":              cfg.get("theme", "dark"),
            "date_format":        cfg.get("date_format", "relative"),
            "default_sort":       cfg.get("default_sort", "ts"),
            "default_filter":     cfg.get("default_filter", "ALL"),
            "show_sessions":      cfg.get("show_sessions", True),
            "compact_default":    cfg.get("compact_default", False),
            "max_events_display": cfg.get("max_events_display", 50),
            "hide_zero_cost":     cfg.get("hide_zero_cost", False),
            "group_by_task":      cfg.get("group_by_task", False),
            "show_token_counts":  cfg.get("show_token_counts", True),
            "cost_precision":     cfg.get("cost_precision", 4),
            "dashboard_title":    cfg.get("dashboard_title", "CostPilot"),
            "model_aliases":      cfg.get("model_aliases", {}),
            "weekly_goal_usd":    weekly_goal,
            "alert_threshold_usd": cfg.get("alert_threshold_usd", 10.0),
            "daily_budget_usd":    cfg.get("daily_budget_usd", 200.0),
            "alert_levels":        cfg.get("alert_levels", {"warn": 150.0, "critical": 200.0}),
            "categories":          cfg.get("categories", CONFIG_DEFAULTS["categories"]),
        },
        "kpi": {
            "today_cost":         round(today_cost * rate, precision),
            "tracked_today_cost": round(tracked_today_cost * rate, precision),
            "gt_today_available": today_iso_early in _gt_daily_early,
            "yesterday_cost": round(yesterday_cost * rate, precision),
            "week_cost":      round(week_cost * rate, precision),
            "month_cost":     round(month_cost * rate, precision),
            "running_cost":   round(running_cost * rate, 6),
            "avg_task_cost":  round(avg_task_cost * rate, precision),
            "projection":     round(projection * rate, 2),
            "tasks_today":    len(today_events),
            "tokens_in":      total_input_today,
            "tokens_out":     total_output_today,
            "tokens_cache":   total_cache_today,
            "token_ratio":    token_ratio,
            "cache_savings":  round(cache_savings, precision),
            "cost_velocity":  round(cost_velocity * rate, 6),
            "session_count_today": session_count_today,
            "daily_budget_remaining": round(daily_budget_remaining, precision),
            "projected_month_cost":   projected_month_cost,
            "weekly_goal_pct":        weekly_goal_pct,
            "forecast_source":        forecast_source,
            "gt_avg_daily_real":      round(gt_avg_daily_real * rate, 2) if gt_avg_daily_real is not None else None,
        },
        "status": {
            "day":           day_status(today_cost * rate),
            "avg":           avg_status(avg_task_cost),
            "projection":    proj_status(projection),
            "has_running":   len(running) > 0,
            "anomaly_count": len(anomaly_list),
            "alert_level":   alert_level,
            "eff_trend":     eff_trend,
            "data_volume_warning": data_volume_warning,
        },
        "running":          running,
        "breakdown":        breakdown,
        "breakdown_week":   breakdown_week,
        "breakdown_month":  breakdown_month,
        "breakdown_by_model":       breakdown_by_model,
        "breakdown_by_model_week":  breakdown_by_model_week,
        "breakdown_by_model_month": breakdown_by_model_month,
        "breakdown_by_hour": breakdown_by_hour,
        "weekly":           weekly_chart,
        "avg_30d":          round(avg_30d, precision),
        "forecast_3d":      forecast_3d,
        "wow_pct":          round(wow_pct, 1),
        "recent":           recent_tasks,
        "anomalies":        anomaly_list,
        "hourly_costs":     hourly_costs,
        "hourly_7d_avg":    hourly_7d_avg,
        "cost_per_hour_7d": cost_per_hour_7d,
        "model_split":      model_split,
        "io_ratios":        io_ratios,
        "trend_pct":        round(trend_pct, 1),
        "peak_task":        peak_task,
        "peak_task_all_time": peak_task_all_time,
        "longest_session":  longest_session,
        "busiest_day":      busiest_day,
        "cost_by_weekday":  cost_by_weekday,
        "task_leaderboard": task_leaderboard[:10],
        "task_frequency":   dict(top_frequent),
        "tags_summary":     tags_summary_list,
        "percentile_stats": percentile_stats,
        "total_cost_all_time":   total_cost_all_time,
        "total_cost_tracked":    total_cost_tracked,
        "total_events_all_time": total_events_all_time,
        "total_events":     total_events_all_time,
        "malformed_lines":  _malformed_count,
        "ground_truth":     _build_ground_truth_section(gt, rate, tracked_today_cost, tracked_week_cost, tracked_month_cost, today_start, week_start, month_start, daily_tracked=daily_cost),
    }


# ── Rate limiter ──────────────────────────────────────────────────────────────
_rate_limits = defaultdict(float)
_rate_lock   = threading.Lock()


def check_rate_limit(key, interval_sec):
    """Return True if allowed, False if rate-limited."""
    with _rate_lock:
        last = _rate_limits.get(key, 0)
        now  = time.time()
        if now - last < interval_sec:
            return False
        _rate_limits[key] = now
        return True


# ── Daily backup thread ───────────────────────────────────────────────────────
_last_backup_day = None


def backup_watcher():
    """Run daily backup at midnight."""
    global _last_backup_day
    while True:
        time.sleep(60)
        today = date.today().isoformat()
        if _last_backup_day != today and datetime.now().hour == 0:
            _do_backup(today)
            _last_backup_day = today
            _maybe_fire_webhook()
            _maybe_auto_archive()


def _do_backup(day_str=None):
    day_str = day_str or date.today().isoformat()
    if not os.path.exists(EVENTS_FILE):
        return
    os.makedirs(BACKUPS_DIR, exist_ok=True)
    dest = os.path.join(BACKUPS_DIR, f"{day_str}.jsonl")
    try:
        shutil.copy2(EVENTS_FILE, dest)
        log_json("info", f"Daily backup → {dest}")
    except Exception as e:
        log_json("error", f"Backup failed: {e}")


def _maybe_fire_webhook():
    """POST daily summary to webhook_url if configured."""
    cfg = load_config(force=True)
    url = cfg.get("webhook_url", "")
    if not url:
        return
    try:
        import urllib.request
        state  = build_state()
        payload = {
            "type": "daily_summary",
            "date": date.today().isoformat(),
            "today_cost": state["kpi"]["today_cost"],
            "week_cost":  state["kpi"]["week_cost"],
            "tasks":      state["kpi"]["tasks_today"],
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=5)
        log_json("info", f"Webhook fired → {url}")
    except Exception as e:
        log_json("error", f"Webhook error: {e}")


def _maybe_auto_archive():
    """Auto-archive events older than retention_days."""
    cfg = load_config(force=True)
    retention = int(cfg.get("retention_days", 90) or 90)
    cutoff    = time.time() - retention * 86400
    _do_archive(cutoff_ts=cutoff)


def _do_archive(cutoff_ts=None):
    """Move events older than cutoff_ts to archive file."""
    if cutoff_ts is None:
        cutoff_ts = time.time() - 30 * 86400
    if not os.path.exists(EVENTS_FILE):
        return {"moved": 0}
    events, _ = load_events(force=True)
    keep    = [e for e in events if e.get("ts", 0) >= cutoff_ts]
    archive = [e for e in events if e.get("ts", 0) < cutoff_ts]
    if not archive:
        return {"moved": 0}
    with _events_write_lock:
        # Append to archive
        with open(ARCHIVE_FILE, "a") as f:
            for e in archive:
                f.write(json.dumps(e) + "\n")
        # Rewrite events file
        with open(EVENTS_FILE, "w") as f:
            for e in keep:
                f.write(json.dumps(e) + "\n")
    load_events(force=True)
    log_json("info", f"Archived {len(archive)} events, kept {len(keep)}")
    return {"moved": len(archive), "kept": len(keep)}


# ── Annotations ───────────────────────────────────────────────────────────────
_anno_lock = threading.Lock()


def load_annotations():
    if not os.path.exists(ANNOTATIONS_FILE):
        return {}
    try:
        with open(ANNOTATIONS_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def save_annotations(data):
    with _anno_lock:
        with open(ANNOTATIONS_FILE, "w") as f:
            json.dump(data, f, indent=2)


# ── Webhook: threshold alert ──────────────────────────────────────────────────
_threshold_alerted = False


def check_threshold_alert():
    """Fire webhook if daily cost exceeds threshold (once per day)."""
    global _threshold_alerted
    cfg = load_config()
    if not cfg.get("notify_on_threshold"):
        return
    url = cfg.get("webhook_url", "")
    if not url:
        return
    try:
        state = _build_state_inner()
        today = state["kpi"]["today_cost"]
        threshold = float(cfg.get("daily_budget_usd", cfg.get("alert_threshold_usd", 200.0))) * float(cfg.get("currency_rate", 1.0))
        if today >= threshold and not _threshold_alerted:
            _threshold_alerted = True
            import urllib.request
            payload = {
                "type": "threshold_alert",
                "today_cost": today,
                "threshold":  threshold,
                "currency":   cfg.get("currency", "USD"),
            }
            req = urllib.request.Request(
                url, data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"}, method="POST"
            )
            urllib.request.urlopen(req, timeout=5)
        elif today < threshold:
            _threshold_alerted = False
    except Exception as e:
        log_json("error", f"Threshold webhook error: {e}")


# ── SIGTERM handling ──────────────────────────────────────────────────────────
def _sigterm_handler(signum, frame):
    log_json("info", "SIGTERM received — shutting down gracefully")
    sys.exit(0)


signal.signal(signal.SIGTERM, _sigterm_handler)


# ── HTTP Handler ──────────────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        pass  # Suppress default; we do custom logging

    def log_request_custom(self, status, elapsed_ms=0, req_id=None):
        log_json("info", f"{self.command} {self.path} → {status} ({elapsed_ms:.1f}ms)",
                 req_id=req_id, ip=self.address_string())

    def do_OPTIONS(self):
        """CORS preflight."""
        self.send_response(204)
        self._cors_headers()
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, PATCH, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.end_headers()

    def _check_token_auth(self, path, qs):
        """
        Check Bearer token auth. Returns True if auth passes (or is not required).
        Sends a 401 response and returns False if auth fails.
        Exempt paths: /, /manifest.json, /api/ping, /api/health
        Disabled entirely when --no-auth flag is set.
        """
        if NO_AUTH:
            return True
        if path in _AUTH_EXEMPT:
            return True
        cfg = load_config()
        token = cfg.get("token", "")
        # Legacy basic_auth still works too
        if not token:
            # If no token configured, also check legacy basic_auth
            ba = cfg.get("basic_auth", {})
            if ba and ba.get("username") and ba.get("password"):
                import base64
                auth_hdr = self.headers.get("Authorization", "")
                expected = base64.b64encode(
                    f"{ba['username']}:{ba['password']}".encode()
                ).decode()
                if auth_hdr == f"Basic {expected}":
                    return True
                self.send_response(401)
                self.send_header("WWW-Authenticate", 'Basic realm="CostPilot"')
                self._cors_headers()
                self.end_headers()
                return False
            # No auth configured at all — allow
            return True
        # Check Authorization: Bearer <token>
        auth_hdr = self.headers.get("Authorization", "")
        if auth_hdr == f"Bearer {token}":
            return True
        # Check ?token=<token> query param
        param_token = qs.get("token", [None])[0] if qs else None
        if param_token == token:
            return True
        body = json.dumps({"error": "Unauthorized", "hint": "Pass Authorization: Bearer <token> header or ?token= param"}).encode()
        self.send_response(401)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self._cors_headers()
        self.end_headers()
        self.wfile.write(body)
        return False

    def do_GET(self):
        t0     = time.perf_counter()
        req_id = str(uuid.uuid4())[:8]
        path   = self.path.split("?")[0]
        qs     = parse_qs(urlparse(self.path).query)

        if not self._check_token_auth(path, qs):
            return

        try:
            self._dispatch_get(path, qs, req_id)
        except Exception as e:
            log_json("error", f"Unhandled exception in GET {path}: {e}", req_id=req_id)
            self._json({"error": "Internal server error", "detail": str(e)}, status=500)

        elapsed = (time.perf_counter() - t0) * 1000
        self.log_request_custom(200, elapsed, req_id)

    def _dispatch_get(self, path, qs, req_id):
        if path == "/":
            self._serve_file(DASHBOARD_FILE, "text/html; charset=utf-8")

        elif path == "/manifest.json":
            self._serve_file(os.path.join(BASE_DIR, "manifest.json"), "application/manifest+json; charset=utf-8")

        elif path == "/api/data":
            tag_filter = qs.get("tag", [None])[0]
            data = build_state()
            if tag_filter:
                data["recent"] = [
                    e for e in data["recent"]
                    if tag_filter in e.get("tags", [])
                ]
            # ETag support
            etag = hashlib.md5(json.dumps(data.get("ts")).encode()).hexdigest()[:12]
            if self.headers.get("If-None-Match") == etag:
                self.send_response(304)
                self.end_headers()
                return
            self._json(data, etag=etag)

        elif path == "/api/live":
            self._sse_handler()

        elif path == "/api/events":
            events, _ = load_events()
            # Date range filter
            from_ts = float(qs.get("from", [0])[0] or 0)
            to_ts   = float(qs.get("to", [time.time() + 86400])[0] or time.time() + 86400)
            if from_ts or to_ts < time.time() + 86400:
                events = [e for e in events if from_ts <= e.get("ts", 0) <= to_ts]
            # Pagination
            page      = int(qs.get("page", [1])[0] or 1)
            page_size = int(qs.get("page_size", [50])[0] or 50)
            limit     = int(qs.get("limit", [0])[0] or 0)
            offset    = int(qs.get("offset", [0])[0] or 0)
            if limit:
                paged = events[-(limit + offset):][:limit]
            else:
                start = (page - 1) * page_size
                paged = events[start:start + page_size]
            self._json({
                "events": paged,
                "total":  len(events),
                "page":   page,
                "page_size": page_size,
            })

        elif path == "/api/config":
            self._json(load_config())

        elif path == "/api/autologger-health":
            self._autologger_health()

        elif path == "/api/export":
            ip = self.address_string()
            if not check_rate_limit(f"export:{ip}", 5):
                self._json({"error": "Rate limited — try again in 5 seconds"}, status=429)
                return
            self._export_csv(qs)

        elif path == "/api/health":
            events, _ = load_events()
            last_ts = max((e.get("ts", 0) for e in events), default=None)
            self._json({
                "status":               "ok",
                "version":              VERSION,
                "events":               len(events),
                "last_event_ts":        last_ts,
                "uptime_sec":           round(time.time() - START_TIME),
                "config_ok":            True,
                "events_file_writable": os.access(os.path.dirname(EVENTS_FILE), os.W_OK),
                "malformed_lines":      _malformed_count,
            })

        elif path == "/api/version":
            self._json({
                "version":        VERSION,
                "build_date":     BUILD_DATE,
                "schema_version": SCHEMA_VER,
            })

        elif path == "/api/ping":
            self._json({"pong": True, "ts": int(time.time())})

        elif path == "/api/archive":
            result = _do_archive()
            load_events(force=True)
            self._json({"ok": True, **result})

        elif path == "/api/backups":
            os.makedirs(BACKUPS_DIR, exist_ok=True)
            backups = sorted([
                {"file": f, "date": f.replace(".jsonl", ""),
                 "size": os.path.getsize(os.path.join(BACKUPS_DIR, f))}
                for f in os.listdir(BACKUPS_DIR)
                if f.endswith(".jsonl")
            ], key=lambda x: x["date"], reverse=True)
            self._json({"backups": backups})

        elif path == "/api/stats":
            events, _ = load_events()
            total_cost = sum(e.get("cost_usd", 0) for e in events)
            dates = [date.fromtimestamp(e.get("ts", 0)).isoformat() for e in events]
            self._json({
                "total_events": len(events),
                "total_cost":   round(total_cost, 4),
                "date_range":   {
                    "from": min(dates) if dates else None,
                    "to":   max(dates) if dates else None,
                },
                "malformed_lines": _malformed_count,
            })

        elif path == "/api/docs":
            self._json(_get_api_docs())

        elif path == "/api/sessions":
            events, _ = load_events()
            sessions = defaultdict(lambda: {"runs": 0, "cost": 0.0})
            for e in events:
                s = e.get("session", "other")
                sessions[s]["runs"] += 1
                sessions[s]["cost"] += e.get("cost_usd", 0)
            result = [{"session": s, "runs": v["runs"], "cost": round(v["cost"], 4)}
                      for s, v in sorted(sessions.items(), key=lambda x: -x[1]["cost"])]
            self._json(result)

        elif path == "/api/compare":
            t1 = (qs.get("task1", [""])[0] or "").strip()
            t2 = (qs.get("task2", [""])[0] or "").strip()
            events, _ = load_events()
            def task_stats(name):
                evts = [e for e in events if e.get("task", "") == name]
                costs = [e.get("cost_usd", 0) for e in evts]
                return {
                    "task": name, "count": len(evts),
                    "avg_cost": round(mean(costs), 4) if costs else 0,
                    "total_cost": round(sum(costs), 4),
                    "p90_cost": round(percentile(costs, 90), 4) if costs else 0,
                }
            self._json({"task1": task_stats(t1), "task2": task_stats(t2)})

        elif path == "/api/timeline":
            date_str = qs.get("date", [date.today().isoformat()])[0]
            try:
                d = date.fromisoformat(date_str)
                start = datetime(d.year, d.month, d.day).timestamp()
                end   = start + 86400
            except ValueError:
                self._json({"error": "Invalid date format"}, status=400)
                return
            events, _ = load_events()
            day_events = sorted(
                [e for e in events if start <= e.get("ts", 0) < end],
                key=lambda e: e.get("ts", 0)
            )
            self._json({"date": date_str, "events": day_events, "count": len(day_events)})

        elif path == "/api/report":
            fmt = qs.get("format", ["markdown"])[0]
            self._generate_report(fmt)

        elif path == "/api/annotations":
            self._json(load_annotations())

        elif path == "/api/estimate":
            model = qs.get("model", ["claude-sonnet-4-6"])[0]
            inp   = int(qs.get("input_tokens", [0])[0] or 0)
            out   = int(qs.get("output_tokens", [0])[0] or 0)
            PRICES = {
                "claude-sonnet-4-6": {"input": 3.0, "output": 15.0},
                "claude-opus-4-6":   {"input": 15.0, "output": 75.0},
                "claude-haiku":      {"input": 0.25, "output": 1.25},
            }
            p = PRICES.get(model, PRICES["claude-sonnet-4-6"])
            cost = (inp / 1_000_000 * p["input"]) + (out / 1_000_000 * p["output"])
            cfg  = load_config()
            rate = float(cfg.get("currency_rate", 1.0))
            self._json({
                "model":    model,
                "input_tokens":  inp,
                "output_tokens": out,
                "cost_usd": round(cost, 6),
                "cost":     round(cost * rate, 6),
                "currency": cfg.get("currency", "USD"),
            })

        elif path.startswith("/api/"):
            self._json({"error": "Not found"}, status=404)

        else:
            self.send_error(404, "Not found")

    def do_POST(self):
        t0     = time.perf_counter()
        req_id = str(uuid.uuid4())[:8]
        path   = self.path.split("?")[0]
        qs     = parse_qs(urlparse(self.path).query)

        if not self._check_token_auth(path, qs):
            return

        try:
            length = int(self.headers.get("Content-Length", 0))
            body   = self.rfile.read(length) if length else b""
            self._dispatch_post(path, qs, body, req_id)
        except Exception as e:
            log_json("error", f"Unhandled exception in POST {path}: {e}", req_id=req_id)
            self._json({"error": "Internal server error", "detail": str(e)}, status=500)

        elapsed = (time.perf_counter() - t0) * 1000
        self.log_request_custom(200, elapsed, req_id)

    def _dispatch_post(self, path, qs, body, req_id):
        if path == "/api/config":
            try:
                incoming = json.loads(body)
            except json.JSONDecodeError:
                self._json({"ok": False, "error": "Invalid JSON"}, status=400)
                return
            cfg = load_config()
            allowed = {
                "user", "project", "currency", "currency_rate",
                "alert_threshold_usd", "daily_budget_usd", "refresh_interval_sec",
                "theme", "date_format", "default_sort", "default_filter",
                "show_sessions", "compact_default", "max_events_display",
                "hide_zero_cost", "group_by_task", "show_token_counts",
                "cost_precision", "dashboard_title", "weekly_goal_usd",
                "model_aliases", "session_label_overrides", "exclude_sessions",
                "webhook_url", "notify_on_threshold", "retention_days",
                "alert_levels",
            }
            # Validate required fields
            for field in ("user", "project"):
                if field in incoming and not isinstance(incoming[field], str):
                    self._json({"ok": False, "error": f"'{field}' must be a string"}, status=400)
                    return
            if "alert_threshold_usd" in incoming:
                try:
                    float(incoming["alert_threshold_usd"])
                except (TypeError, ValueError):
                    self._json({"ok": False, "error": "alert_threshold_usd must be a number"}, status=400)
                    return
            if "daily_budget_usd" in incoming:
                try:
                    float(incoming["daily_budget_usd"])
                except (TypeError, ValueError):
                    self._json({"ok": False, "error": "daily_budget_usd must be a number"}, status=400)
                    return
            for field in allowed:
                if field in incoming:
                    cfg[field] = incoming[field]
            try:
                with open(CONFIG_FILE, "w") as f:
                    json.dump(cfg, f, indent=2)
                load_config(force=True)
                # Invalidate state cache
                global _state_cache, _state_cache_ts
                _state_cache = None
                self._json({"ok": True})
            except Exception as e:
                self._json({"ok": False, "error": str(e)}, status=500)

        elif path == "/api/import":
            self._import_events(body)

        elif path == "/api/restore":
            try:
                data = json.loads(body)
            except Exception:
                self._json({"error": "Invalid JSON"}, status=400)
                return
            fname = data.get("file", "")
            src   = os.path.join(BACKUPS_DIR, os.path.basename(fname))
            if not os.path.exists(src):
                self._json({"error": "Backup file not found"}, status=404)
                return
            try:
                shutil.copy2(src, EVENTS_FILE)
                load_events(force=True)
                self._json({"ok": True, "restored": fname})
            except Exception as e:
                self._json({"error": str(e)}, status=500)

        elif path == "/api/notify":
            try:
                payload = json.loads(body) if body else {}
            except Exception:
                payload = {}
            self._json({"ok": True, "payload": payload})

        elif path == "/api/annotations":
            try:
                data = json.loads(body)
            except Exception:
                self._json({"error": "Invalid JSON"}, status=400)
                return
            if not data.get("event_id") or not data.get("text"):
                self._json({"error": "event_id and text required"}, status=400)
                return
            annos = load_annotations()
            anno_id = str(uuid.uuid4())[:8]
            annos[anno_id] = {
                "id": anno_id,
                "event_id": data["event_id"],
                "text":     data["text"],
                "ts":       time.time(),
            }
            save_annotations(annos)
            self._json({"ok": True, "id": anno_id})

        else:
            self._json({"error": "Not found"}, status=404)

    def do_DELETE(self):
        t0   = time.perf_counter()
        path = self.path.split("?")[0]
        qs   = parse_qs(urlparse(self.path).query)

        if not self._check_token_auth(path, qs):
            return

        try:
            length = int(self.headers.get("Content-Length", 0))
            body   = self.rfile.read(length) if length else b""

            if path == "/api/clear":
                try:
                    data = json.loads(body) if body else {}
                except Exception:
                    data = {}
                token = data.get("token") or qs.get("token", [None])[0]
                if token != "CONFIRM":
                    self._json({"error": "Token 'CONFIRM' required"}, status=400)
                    return
                if os.path.exists(EVENTS_FILE):
                    # Backup first
                    _do_backup(f"{date.today().isoformat()}-pre-clear")
                    os.remove(EVENTS_FILE)
                load_events(force=True)
                global _state_cache, _state_cache_ts
                _state_cache = None
                self._json({"ok": True, "cleared": True})

            elif re.match(r"^/api/annotations/[a-f0-9]+$", path):
                anno_id = path.split("/")[-1]
                annos   = load_annotations()
                if anno_id in annos:
                    del annos[anno_id]
                    save_annotations(annos)
                    self._json({"ok": True})
                else:
                    self._json({"error": "Annotation not found"}, status=404)

            else:
                self._json({"error": "Not found"}, status=404)

        except Exception as e:
            self._json({"error": str(e)}, status=500)

    def do_PATCH(self):
        path = self.path.split("?")[0]
        qs   = parse_qs(urlparse(self.path).query)
        if not self._check_token_auth(path, qs):
            return

        # ── Bulk rename all events sharing the same task name ─────────────
        m2 = re.match(r"^/api/tasks/rename$", path)
        if m2:
            try:
                length   = int(self.headers.get("Content-Length", 0))
                body     = self.rfile.read(length)
                data     = json.loads(body)
                old_name = data.get("old", "").strip()
                new_name = data.get("new", "").strip()
            except Exception:
                self._json({"error": "Invalid body"}, status=400)
                return
            if not old_name or not new_name:
                self._json({"error": "old and new task names required"}, status=400)
                return

            with _events_write_lock:
                if not os.path.exists(EVENTS_FILE):
                    self._json({"error": "No events file"}, status=404)
                    return
                lines   = []
                renamed = 0
                with open(EVENTS_FILE) as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            ev = json.loads(line)
                            if ev.get("task") == old_name:
                                ev["task"] = new_name
                                renamed += 1
                            lines.append(ev)
                        except json.JSONDecodeError:
                            pass
                with open(EVENTS_FILE, "w") as f:
                    for ev in lines:
                        f.write(json.dumps(ev) + "\n")

            load_events(force=True)
            self._json({"ok": True, "renamed": renamed})
            return

        # ── Single-event rename ───────────────────────────────────────────
        m    = re.match(r"^/api/events/([a-f0-9]+)/rename$", path)
        if not m:
            self._json({"error": "Not found"}, status=404)
            return
        ev_id = m.group(1)
        try:
            length   = int(self.headers.get("Content-Length", 0))
            body     = self.rfile.read(length)
            data     = json.loads(body)
            new_name = data.get("task", "").strip()
        except Exception:
            self._json({"error": "Invalid body"}, status=400)
            return
        if not new_name:
            self._json({"error": "task name required"}, status=400)
            return

        with _events_write_lock:
            if not os.path.exists(EVENTS_FILE):
                self._json({"error": "No events file"}, status=404)
                return
            lines = []
            renamed = False
            with open(EVENTS_FILE) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        ev = json.loads(line)
                        if event_id(ev) == ev_id or ev.get("id") == ev_id:
                            ev["task"] = new_name
                            renamed = True
                        lines.append(ev)
                    except json.JSONDecodeError:
                        pass
            if not renamed:
                self._json({"error": "Event not found"}, status=404)
                return
            with open(EVENTS_FILE, "w") as f:
                for ev in lines:
                    f.write(json.dumps(ev) + "\n")

        load_events(force=True)
        self._json({"ok": True, "renamed": renamed})

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _json(self, data, status=200, etag=None):
        """Send JSON response with standard headers."""
        body = json.dumps(data, indent=2, default=str).encode("utf-8")

        # Gzip if client accepts it and body is large
        accept_enc = self.headers.get("Accept-Encoding", "")
        use_gzip   = "gzip" in accept_enc and len(body) > 2048

        if use_gzip:
            buf = io.BytesIO()
            with gzip.GzipFile(fileobj=buf, mode="wb") as gz:
                gz.write(body)
            body = buf.getvalue()

        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Request-ID", str(uuid.uuid4())[:8])
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        self.send_header("X-Content-Type-Options", "nosniff")
        if use_gzip:
            self.send_header("Content-Encoding", "gzip")
        if etag:
            self.send_header("ETag", etag)
        self._cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def _cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")

    def _serve_file(self, filepath, content_type):
        if not os.path.exists(filepath):
            self.send_error(404, f"File not found: {filepath}")
            return
        with open(filepath, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _autologger_health(self):
        age_sec  = None
        last_run = None
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE) as f:
                    state = json.load(f)
                ts_vals = [v.get("ts", 0) for v in state.values() if isinstance(v, dict)]
                if ts_vals:
                    last_ts  = max(ts_vals)
                    last_run = datetime.fromtimestamp(last_ts).strftime("%Y-%m-%d %H:%M:%S")
                    age_sec  = int(time.time()) - last_ts
            except Exception:
                pass
        self._json({"last_run": last_run, "age_sec": age_sec})

    def _export_csv(self, qs):
        events, _ = load_events()
        fmt = qs.get("format", ["csv"])[0]

        if fmt == "markdown":
            lines = ["| Time | Task | Model | Session | Cost | Tokens In | Tokens Out | Duration |",
                     "|------|------|-------|---------|------|-----------|------------|----------|"]
            for e in events:
                dt = datetime.fromtimestamp(e.get("ts", 0)).strftime("%Y-%m-%d %H:%M")
                lines.append(
                    f"| {dt} | {e.get('task','')} | {e.get('model','')} | {e.get('session','')} "
                    f"| ${e.get('cost_usd',0):.4f} | {e.get('input_tokens',0)} "
                    f"| {e.get('output_tokens',0)} | {e.get('duration_sec',0)}s |"
                )
            body     = "\n".join(lines).encode("utf-8")
            filename = f"costpilot-costs-{date.today().isoformat()}.md"
            ct       = "text/markdown; charset=utf-8"
        elif fmt == "json":
            body     = json.dumps(events, indent=2).encode("utf-8")
            filename = f"costpilot-costs-{date.today().isoformat()}.json"
            ct       = "application/json; charset=utf-8"
        else:
            out = io.StringIO()
            out.write("timestamp,datetime,task,model,session,status,cost_usd,input_tokens,output_tokens,cache_read_tokens,duration_sec\n")
            for e in events:
                dt = datetime.fromtimestamp(e.get("ts", 0)).strftime("%Y-%m-%d %H:%M:%S")
                row = [
                    str(e.get("ts", "")), dt,
                    '"' + str(e.get("task", "")).replace('"', '""') + '"',
                    e.get("model", ""), e.get("session", ""), e.get("status", ""),
                    str(round(e.get("cost_usd", 0), 6)),
                    str(e.get("input_tokens", 0)), str(e.get("output_tokens", 0)),
                    str(e.get("cache_read_tokens", 0)), str(e.get("duration_sec", 0)),
                ]
                out.write(",".join(row) + "\n")
            body     = out.getvalue().encode("utf-8")
            filename = f"costpilot-costs-{date.today().isoformat()}.csv"
            ct       = "text/csv; charset=utf-8"

        self.send_response(200)
        self.send_header("Content-Type", ct)
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self._cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def _import_events(self, body):
        """Append validated, deduplicated events from JSONL body."""
        events, _ = load_events()
        existing_ids = {event_id(e) for e in events}
        REQUIRED = {"ts", "task", "cost_usd"}

        new_events = []
        bad = 0
        dupes = 0
        lines = body.decode("utf-8", errors="replace").splitlines()
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                bad += 1
                continue
            if not isinstance(ev, dict) or not REQUIRED.issubset(ev.keys()):
                bad += 1
                continue
            eid = event_id(ev)
            if eid in existing_ids:
                dupes += 1
                continue
            ev["id"] = eid
            existing_ids.add(eid)
            new_events.append(ev)

        if new_events:
            write_events_locked(new_events)
            load_events(force=True)
            global _state_cache, _state_cache_ts
            _state_cache = None

        self._json({
            "ok": True,
            "imported": len(new_events),
            "skipped_malformed": bad,
            "skipped_dupes": dupes,
        })

    def _generate_report(self, fmt):
        """Generate weekly summary report."""
        state = build_state()
        kpi   = state["kpi"]
        now   = datetime.now()

        if fmt == "markdown":
            lines = [
                f"# CostPilot Report — Week of {now.strftime('%Y-%m-%d')}",
                "",
                "## Summary",
                f"- **This week:** ${kpi['week_cost']:.4f}",
                f"- **Today:** ${kpi['today_cost']:.4f}",
                f"- **Tasks today:** {kpi['tasks_today']}",
                f"- **Average per task:** ${kpi['avg_task_cost']:.4f}",
                f"- **Projection (24h):** ${kpi['projection']:.2f}",
                "",
                "## Cost by Session (Today)",
                "| Session | Cost | Runs |",
                "|---------|------|------|",
            ]
            for b in state.get("breakdown", []):
                lines.append(f"| {b['session']} | ${b['cost']:.4f} | {b['runs']} |")
            lines += [
                "",
                "## Recent Events",
                "| Time | Task | Cost | Duration |",
                "|------|------|------|----------|",
            ]
            for e in state.get("recent", [])[:20]:
                dt = datetime.fromtimestamp(e["ts"]).strftime("%H:%M")
                lines.append(f"| {dt} | {e['task']} | ${e['cost']:.4f} | {e.get('duration_sec',0)}s |")
            lines += ["", f"*Generated by CostPilot v{VERSION}*"]
            body     = "\n".join(lines).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/markdown; charset=utf-8")
            self.send_header("Content-Disposition",
                             f'attachment; filename="costpilot-report-{now.strftime("%Y-%m-%d")}.md"')
            self.send_header("Content-Length", str(len(body)))
            self._cors_headers()
            self.end_headers()
            self.wfile.write(body)
        else:
            self._json(state)

    def _sse_handler(self):
        """Server-Sent Events stream with delta compression."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self._cors_headers()
        self.end_headers()

        import queue
        q = queue.Queue()
        with _sse_lock:
            _sse_clients.append(q)

        try:
            data = build_state()
            msg  = f"data: {json.dumps(data)}\n\n"
            self.wfile.write(msg.encode("utf-8"))
            self.wfile.flush()

            while True:
                try:
                    msg = q.get(timeout=30)
                    self.wfile.write(msg.encode("utf-8"))
                    self.wfile.flush()
                except Exception:
                    self.wfile.write(b": heartbeat\n\n")
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            with _sse_lock:
                if q in _sse_clients:
                    _sse_clients.remove(q)


def _get_api_docs():
    """Return a simple API schema for all endpoints."""
    return {
        "version": VERSION,
        "base_url": f"http://localhost:{PORT}",
        "endpoints": [
            {"method": "GET",    "path": "/",                          "description": "Dashboard HTML"},
            {"method": "GET",    "path": "/api/data",                  "description": "Full analytics state", "params": ["tag"]},
            {"method": "GET",    "path": "/api/live",                  "description": "SSE stream"},
            {"method": "GET",    "path": "/api/events",                "description": "Paginated events", "params": ["page", "page_size", "limit", "offset", "from", "to"]},
            {"method": "GET",    "path": "/api/config",                "description": "Current config"},
            {"method": "POST",   "path": "/api/config",                "description": "Update config"},
            {"method": "GET",    "path": "/api/health",                "description": "Health check"},
            {"method": "GET",    "path": "/api/version",               "description": "Version info"},
            {"method": "GET",    "path": "/api/ping",                  "description": "Trivial health probe"},
            {"method": "GET",    "path": "/api/export",                "description": "CSV/JSON/Markdown export", "params": ["format"]},
            {"method": "GET",    "path": "/api/autologger-health",     "description": "Auto-logger last run"},
            {"method": "GET",    "path": "/api/archive",               "description": "Archive events older than 30 days"},
            {"method": "POST",   "path": "/api/import",                "description": "Import JSONL events"},
            {"method": "DELETE", "path": "/api/clear",                 "description": "Clear all events (token=CONFIRM)"},
            {"method": "GET",    "path": "/api/backups",               "description": "List backup files"},
            {"method": "POST",   "path": "/api/restore",               "description": "Restore from backup"},
            {"method": "GET",    "path": "/api/stats",                 "description": "Aggregate statistics"},
            {"method": "GET",    "path": "/api/docs",                  "description": "This document"},
            {"method": "GET",    "path": "/api/sessions",              "description": "All session keys"},
            {"method": "GET",    "path": "/api/compare",               "description": "Compare two tasks", "params": ["task1", "task2"]},
            {"method": "GET",    "path": "/api/timeline",              "description": "Day timeline", "params": ["date"]},
            {"method": "GET",    "path": "/api/report",                "description": "Weekly report", "params": ["format"]},
            {"method": "POST",   "path": "/api/notify",                "description": "Trigger notification"},
            {"method": "GET",    "path": "/api/annotations",           "description": "List annotations"},
            {"method": "POST",   "path": "/api/annotations",           "description": "Add annotation"},
            {"method": "DELETE", "path": "/api/annotations/{id}",      "description": "Delete annotation"},
            {"method": "GET",    "path": "/api/estimate",              "description": "Cost estimate", "params": ["model", "input_tokens", "output_tokens"]},
            {"method": "PATCH",  "path": "/api/events/{hash}/rename",  "description": "Rename task"},
        ],
    }


# ── SSE Broadcaster ───────────────────────────────────────────────────────────
def sse_broadcaster():
    """Push state to all SSE clients at the configured interval."""
    while True:
        cfg      = load_config()
        interval = max(1, int(cfg.get("refresh_interval_sec", 2) or 2))
        time.sleep(interval)
        if not _sse_clients:
            continue
        try:
            data = build_state()
            check_threshold_alert()
            msg  = f"data: {json.dumps(data, default=str)}\n\n"
            with _sse_lock:
                dead = []
                for q in _sse_clients:
                    try:
                        q.put_nowait(msg)
                    except Exception:
                        dead.append(q)
                for q in dead:
                    _sse_clients.remove(q)
        except Exception as e:
            log_json("error", f"SSE broadcast error: {e}")


# ── ThreadingHTTPServer ────────────────────────────────────────────────────────
class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    """Handle each request in a new thread."""
    daemon_threads    = True
    allow_reuse_address = True


# ── Auth globals ─────────────────────────────────────────────────────────────
NO_AUTH = False  # set via --no-auth CLI flag


def _ensure_token():
    """Generate a random Bearer token if not set in config.json. Returns the token."""
    import secrets
    cfg_path = CONFIG_FILE
    cfg_data = {}
    if os.path.exists(cfg_path):
        try:
            with open(cfg_path) as f:
                cfg_data = json.load(f)
        except Exception:
            pass
    token = cfg_data.get("token", "")
    if not token:
        token = secrets.token_hex(24)
        cfg_data["token"] = token
        try:
            with open(cfg_path, "w") as f:
                json.dump(cfg_data, f, indent=2)
        except Exception as e:
            print(f"  ⚠ Could not save token to config: {e}", file=sys.stderr)
    return token


# Endpoints that bypass token auth entirely
_AUTH_EXEMPT = {"/", "/manifest.json", "/api/ping", "/api/health"}


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    global PORT, HOST, EVENTS_FILE, CONFIG_FILE, JSON_LOG, NO_AUTH

    parser = argparse.ArgumentParser(description="CostPilot — AI Spend Monitoring Server")
    parser.add_argument("--port",        type=int,   default=PORT,        help="Port to listen on (default: 8742)")
    parser.add_argument("--host",        type=str,   default=HOST,        help="Host to bind (default: 0.0.0.0)")
    parser.add_argument("--data-file",   type=str,   default=EVENTS_FILE, help="Path to cost-events.jsonl")
    parser.add_argument("--config-file", type=str,   default=CONFIG_FILE, help="Path to config.json")
    parser.add_argument("--json-log",    action="store_true",             help="Emit structured JSON log lines")
    parser.add_argument("--no-auth",     action="store_true",             help="Disable Bearer token auth (local dev)")
    args = parser.parse_args()

    PORT        = args.port
    HOST        = args.host
    EVENTS_FILE = args.data_file
    CONFIG_FILE = args.config_file
    JSON_LOG    = args.json_log
    NO_AUTH     = args.no_auth

    # Ensure Bearer token exists (generate + save on first run) unless --no-auth
    if NO_AUTH:
        print("  ⚠  Auth disabled (--no-auth). Do not expose to the internet.")
    else:
        token = _ensure_token()
        print(f"  🔑 Bearer token: {token}")
        print(f"     Pass as header:  Authorization: Bearer {token}")
        print(f"     Pass as param:   ?token={token}")

    # Initial backup if needed
    _do_backup()

    # Start SSE broadcaster
    threading.Thread(target=sse_broadcaster, daemon=True).start()
    # Start backup/webhook watcher
    threading.Thread(target=backup_watcher, daemon=True).start()

    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"⚡ CostPilot v{VERSION} running at http://localhost:{PORT}")
    print(f"   Dashboard:  http://localhost:{PORT}/")
    print(f"   API:        http://localhost:{PORT}/api/data")
    print(f"   SSE stream: http://localhost:{PORT}/api/live")
    print(f"   Health:     http://localhost:{PORT}/api/health")
    print(f"   Docs:       http://localhost:{PORT}/api/docs")
    print(f"   Events:     {EVENTS_FILE}")
    print(f"   Config:     {CONFIG_FILE}")
    print()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log_json("info", "KeyboardInterrupt — shutting down")


if __name__ == "__main__":
    main()
