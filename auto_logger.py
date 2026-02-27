#!/usr/bin/env python3
"""
CostPilot — Auto-Logger v2.0

Multi-provider AI cost ingestion engine.

Supported input modes (--mode):
  openclaw  [default] Read OpenClaw session JSONL files (Anthropic exact costs)
  csv                 Import from Anthropic-exported CSV (date,input_tokens,...)
  openai              Fetch from OpenAI Usage API (requires OPENAI_API_KEY)

openclaw mode (v2.0 approach):
  - Scan sessions dir (default ~/.openclaw/agents/main/sessions/) for *.jsonl
  - Per message: read usage.cost.total + full token breakdown
  - Each message is attributed to its own timestamp (not session-end)
  - Delta-only: state file tracks last-processed timestamp per JSONL file

csv mode:
  - Columns: date, input_tokens, output_tokens,
             cache_creation_input_tokens, cache_read_input_tokens, cost
  - One event per CSV row, timestamp = date midnight UTC

openai mode:
  - GET https://api.openai.com/v1/usage?date=YYYY-MM-DD
  - Reads n_context_tokens_total (input) + n_generated_tokens_total (output)
  - Pricing configurable in config.json under openai_pricing
  - Defaults: gpt-4o input $2.50/M, output $10/M
"""

import argparse
import csv
import fcntl
import json
import os
import glob
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone, date as date_cls

# ── Paths ─────────────────────────────────────────────────────────────────────
DEFAULT_SESSIONS_DIR  = os.path.expanduser("~/.openclaw/agents/main/sessions/")
DEFAULT_CRON_JOBS_FILE = os.path.expanduser("~/.openclaw/cron/jobs.json")
DEFAULT_EVENTS_FILE   = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cost-events.jsonl")
DEFAULT_STATE_FILE    = os.path.join(os.path.dirname(os.path.abspath(__file__)), "auto-logger-state.json")
DEFAULT_CONFIG_FILE   = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
ERRORS_LOG_FILE       = os.path.join(os.path.dirname(os.path.abspath(__file__)), "auto-logger-errors.log")
LAST_RUN_FILE         = os.path.join(os.path.dirname(os.path.abspath(__file__)), "auto-logger-last-run.json")
PID_FILE              = os.path.join(os.path.dirname(os.path.abspath(__file__)), "auto-logger.pid")

# ── Session label mapping ──────────────────────────────────────────────────────
SESSION_NAMES = {
    "agent:main:main": "KIRA",
}

# sessions.json key → JSONL filename mapping (loaded dynamically from sessions.json)
_session_key_to_uuid = {}

# ── Helpers ───────────────────────────────────────────────────────────────────
_verbose = False
_quiet   = False

def vprint(*a, **k):
    if _verbose and not _quiet: print(*a, **k)

def qprint(*a, **k):
    if not _quiet: print(*a, **k)

def log_error(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] ERROR: {msg}\n"
    try:
        with open(ERRORS_LOG_FILE, "a") as f:
            f.write(line)
    except Exception:
        pass
    if not _quiet:
        print(line.rstrip(), file=sys.stderr)


# ── Config ────────────────────────────────────────────────────────────────────
def load_config(config_file=None):
    fpath = config_file or DEFAULT_CONFIG_FILE
    defaults = {"session_label_overrides": {}, "exclude_sessions": [], "model_aliases": {}}
    if os.path.exists(fpath):
        try:
            with open(fpath) as f:
                defaults.update(json.load(f))
        except Exception as e:
            log_error(f"Config load error: {e}")
    return defaults


# ── Cron names ────────────────────────────────────────────────────────────────
def load_cron_names(cron_file=None):
    fpath = cron_file or DEFAULT_CRON_JOBS_FILE
    try:
        with open(fpath) as f:
            data = json.load(f)
        return {j["id"]: j.get("name", f"Cron {j['id'][:8]}") for j in data.get("jobs", [])}
    except Exception:
        return {}


# ── Session label ─────────────────────────────────────────────────────────────
def session_label(session_key, cron_names=None, overrides=None):
    """Map a session key or JSONL UUID to a human-readable label."""
    if overrides and session_key in overrides:
        return overrides[session_key]
    if session_key in SESSION_NAMES:
        return SESSION_NAMES[session_key]

    cron_names = cron_names or {}

    # UUID-based lookup (for JSONL filenames)
    import re
    uuids = re.findall(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', session_key)
    for uid in uuids:
        if uid in cron_names:
            return cron_names[uid]

    if "main" in session_key and "cron" not in session_key:
        return "KIRA"

    for uid in uuids:
        # Try partial match in key → cron name
        for cron_id, name in cron_names.items():
            if uid in cron_id or cron_id in uid:
                return name

    return f"Session {session_key[:8]}"  # fallback — enriched in _run() if JSONL available


# ── Smart label from JSONL content ───────────────────────────────────────────
def smart_label_from_jsonl(fpath):
    """
    Read the first cost event from a session JSONL to infer a meaningful label.
    Returns something like "Sonnet · Feb 27 04:00" or None if unable.
    """
    MODEL_SHORT = {
        "sonnet": "Sonnet",
        "opus":   "Opus",
        "haiku":  "Haiku",
    }
    try:
        with open(fpath) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                    msg = d.get("message", {})
                    if not isinstance(msg, dict):
                        continue
                    usage = msg.get("usage", {})
                    if not usage or not usage.get("cost", {}).get("total"):
                        continue
                    # Get model
                    model_raw = (msg.get("model") or "").lower()
                    model_short = "AI"
                    for key, label in MODEL_SHORT.items():
                        if key in model_raw:
                            model_short = label
                            break
                    # Get timestamp
                    ts_raw = d.get("timestamp")
                    ts = parse_ts(ts_raw)
                    if ts:
                        dt = datetime.fromtimestamp(ts)
                        months = ["Jan","Feb","Mar","Apr","May","Jun",
                                  "Jul","Aug","Sep","Oct","Nov","Dec"]
                        month_str = months[dt.month - 1]
                        return f"{model_short} · {month_str} {dt.day} {dt.hour:02d}:00"
                except Exception:
                    continue
    except Exception:
        pass
    return None


# ── Build session-key → UUID mapping from sessions.json ───────────────────────
def build_uuid_map(sessions_dir=None):
    """
    Read sessions.json to map session keys → session UUIDs (JSONL filenames).
    sessions.json format: { "agent:main:main": { "sessionId": "...", ... }, ... }
    """
    sdir = sessions_dir or DEFAULT_SESSIONS_DIR
    sfile = os.path.join(sdir, "sessions.json")
    mapping = {}  # session_key → uuid
    rev_mapping = {}  # uuid → session_key
    if not os.path.exists(sfile):
        return mapping, rev_mapping
    try:
        with open(sfile) as f:
            data = json.load(f)
        for key, val in data.items():
            if isinstance(val, dict):
                sid = val.get("sessionId")
                if sid:
                    mapping[key] = sid
                    rev_mapping[sid] = key
    except Exception as e:
        log_error(f"sessions.json read error: {e}")
    return mapping, rev_mapping


# ── State ─────────────────────────────────────────────────────────────────────
def load_state(state_file=None):
    fpath = state_file or DEFAULT_STATE_FILE
    if os.path.exists(fpath):
        try:
            with open(fpath) as f:
                return json.load(f)
        except Exception as e:
            log_error(f"State load error: {e}")
    return {}

def save_state(state, state_file=None):
    fpath = state_file or DEFAULT_STATE_FILE
    try:
        with open(fpath, "w") as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        log_error(f"State save error: {e}")


# ── PID lock ──────────────────────────────────────────────────────────────────
def acquire_pid_lock():
    if os.path.exists(PID_FILE):
        try:
            with open(PID_FILE) as f:
                old_pid = int(f.read().strip())
            os.kill(old_pid, 0)
            qprint(f"  Another instance is running (PID {old_pid}). Exiting.")
            return False
        except (ValueError, ProcessLookupError, OSError):
            pass
    try:
        with open(PID_FILE, "w") as f:
            f.write(str(os.getpid()))
        return True
    except Exception:
        return True

def release_pid_lock():
    try:
        if os.path.exists(PID_FILE):
            os.remove(PID_FILE)
    except Exception:
        pass


# ── Events file ───────────────────────────────────────────────────────────────
def write_events_locked(events_file, new_events):
    with open(events_file, "a") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX)
            for ev in new_events:
                f.write(json.dumps(ev) + "\n")
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


# ── Parse timestamp ───────────────────────────────────────────────────────────
def parse_ts(raw):
    """Return Unix timestamp (float) from ISO string, or None."""
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        return dt.timestamp()
    except Exception:
        return None


# ── Read a session JSONL file, return new cost events ─────────────────────────
def process_jsonl(fpath, last_ts, label, dry_run=False):
    """
    Read a session JSONL file and return list of cost events for messages
    newer than last_ts (Unix float). Also returns the new max_ts seen.

    Each returned event dict:
      ts, task, model, input_tokens, output_tokens,
      cache_read_tokens, cache_write_tokens, cost_usd,
      status, session
    """
    events = []
    new_max_ts = last_ts

    try:
        with open(fpath) as f:
            lines = f.readlines()
    except Exception as e:
        log_error(f"JSONL read error {fpath}: {e}")
        return events, new_max_ts

    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue

        msg = d.get("message", {})
        if not isinstance(msg, dict):
            continue

        usage = msg.get("usage")
        if not usage:
            continue

        cost_block = usage.get("cost", {})
        if not isinstance(cost_block, dict):
            continue

        total_cost = cost_block.get("total", 0)
        if not total_cost:
            continue  # message with no cost (user turns, etc.)

        ts_raw = d.get("timestamp")
        msg_ts = parse_ts(ts_raw)
        if msg_ts is None:
            continue

        # Skip already-processed messages
        if last_ts and msg_ts <= last_ts:
            continue

        # Track new max
        if new_max_ts is None or msg_ts > new_max_ts:
            new_max_ts = msg_ts

        # Extract token breakdown
        input_tokens      = usage.get("input", 0)
        output_tokens     = usage.get("output", 0)
        cache_read_tokens = usage.get("cacheRead", 0)
        cache_write_tokens = usage.get("cacheWrite", 0)

        # Model from message if available
        model = msg.get("model", "claude-sonnet-4-6")

        event = {
            "ts":                  int(msg_ts),
            "task":                label,
            "model":               model,
            "input_tokens":        input_tokens,
            "output_tokens":       output_tokens,
            "cache_read_tokens":   cache_read_tokens,
            "cache_write_tokens":  cache_write_tokens,
            "cost_usd":            round(total_cost, 6),
            "status":              "completed",
            "session":             os.path.basename(fpath).replace(".jsonl", ""),
            "source":              "jsonl-exact",
        }
        events.append(event)

    return events, new_max_ts


# ── Export CSV ────────────────────────────────────────────────────────────────
def export_csv(events_file, output_file):
    try:
        events = []
        with open(events_file) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        events.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
        fields = ["ts", "task", "model", "input_tokens", "output_tokens",
                  "cache_read_tokens", "cache_write_tokens", "cost_usd", "status", "session"]
        with open(output_file, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(events)
        qprint(f"  Exported {len(events)} events → {output_file}")
    except Exception as e:
        log_error(f"CSV export failed: {e}")
        sys.exit(1)


# ── Daily summary from events file ───────────────────────────────────────────
def print_daily_summary(events_file, days=7):
    """Print last N days cost summary from events file."""
    daily = {}
    try:
        with open(events_file) as f:
            for line in f:
                line = line.strip()
                if not line: continue
                try:
                    e = json.loads(line)
                    ts = e.get("ts", 0)
                    if not ts: continue
                    day = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
                    daily[day] = daily.get(day, 0) + e.get("cost_usd", 0)
                except: pass
    except Exception:
        return
    qprint("\n📅 Daily summary (last 7 days):")
    for day in sorted(daily.keys())[-days:]:
        qprint(f"   {day}: ${daily[day]:.4f}")


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    global _verbose, _quiet

    parser = argparse.ArgumentParser(
        description="CostPilot Auto-Logger v2.0 — multi-provider AI cost ingestion",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Input Modes (--mode):
  openclaw  [default] Read OpenClaw session JSONL files
                      Source: --sessions-dir (or sessions_dir in config.json)
  csv                 Import from Anthropic-exported CSV file
                      Source: --csv-file FILE
                      Columns: date,input_tokens,output_tokens,
                               cache_creation_input_tokens,cache_read_input_tokens,cost
  openai              Fetch from OpenAI Usage API
                      Source: --openai-api-key KEY (or OPENAI_API_KEY env var, or config.json)
                      Options: --date YYYY-MM-DD (default: today)

Examples:
  python3 auto_logger.py                              # openclaw mode, default sessions dir
  python3 auto_logger.py --mode openclaw --dry-run
  python3 auto_logger.py --mode csv --csv-file export.csv
  python3 auto_logger.py --mode openai --date 2026-02-27
  python3 auto_logger.py --sessions-dir /custom/path/sessions/
""",
    )
    parser.add_argument("--mode",           type=str, default="openclaw",
                        choices=["openclaw", "csv", "openai"],
                        help="Input mode: openclaw (default), csv, openai")
    parser.add_argument("--sessions-dir",   type=str, default=None,
                        help="Sessions dir for openclaw mode (overrides config.json sessions_dir)")
    parser.add_argument("--csv-file",       type=str, default=None,
                        help="CSV file path for --mode csv")
    parser.add_argument("--openai-api-key", type=str, default=None,
                        help="OpenAI API key for --mode openai (overrides env + config)")
    parser.add_argument("--date",           type=str, default=None,
                        help="Date for --mode openai (YYYY-MM-DD, default: today)")
    parser.add_argument("--output-file",    type=str, default=DEFAULT_EVENTS_FILE)
    parser.add_argument("--config-file",    type=str, default=DEFAULT_CONFIG_FILE)
    parser.add_argument("--state-file",     type=str, default=DEFAULT_STATE_FILE)
    parser.add_argument("--dry-run",        action="store_true")
    parser.add_argument("--verbose",        action="store_true")
    parser.add_argument("--quiet",          action="store_true")
    parser.add_argument("--reset-state",    action="store_true",
                        help="Clear state and start fresh (re-imports everything)")
    parser.add_argument("--export-csv",     type=str, metavar="FILE")
    parser.add_argument("--summary",        action="store_true",
                        help="Print daily cost summary and exit")
    args = parser.parse_args()

    # Resolve sessions_dir: CLI > config.json > default
    cfg_early = load_config(args.config_file)
    if args.sessions_dir is None:
        cfg_sessions_dir = cfg_early.get("sessions_dir", "")
        if cfg_sessions_dir:
            args.sessions_dir = os.path.expanduser(cfg_sessions_dir)
        else:
            args.sessions_dir = DEFAULT_SESSIONS_DIR

    _verbose = args.verbose
    _quiet   = args.quiet

    run_start = time.time()
    qprint(f"⚡ CostPilot Auto-Logger v2.0 [{args.mode}] — {time.strftime('%Y-%m-%d %H:%M:%S')}")

    if args.export_csv:
        export_csv(args.output_file, args.export_csv)
        return

    if args.summary:
        print_daily_summary(args.output_file)
        return

    if args.reset_state:
        if os.path.exists(args.state_file):
            os.remove(args.state_file)
            qprint("  ✓ State reset. Next run will re-process all session messages.")
        else:
            qprint("  State file not found (already clean).")
        return

    if args.mode in ("openclaw",):
        if not acquire_pid_lock():
            sys.exit(1)
        try:
            _run(args, run_start)
        finally:
            release_pid_lock()
    elif args.mode == "csv":
        _run_csv(args, run_start)
    elif args.mode == "openai":
        _run_openai(args, run_start)


def _run(args, run_start):
    cfg        = load_config(args.config_file)
    cron_names = load_cron_names()
    overrides  = cfg.get("session_label_overrides", {}) or {}
    exclude    = set(cfg.get("exclude_sessions", []) or [])

    # Build UUID ↔ session-key mapping
    key_to_uuid, uuid_to_key = build_uuid_map(args.sessions_dir)
    vprint(f"  Session map: {len(key_to_uuid)} entries")

    # Load state: { uuid: last_processed_ts (float) }
    state = load_state(args.state_file)

    # Find all JSONL files
    pattern = os.path.join(args.sessions_dir, "*.jsonl")
    jsonl_files = sorted(glob.glob(pattern))
    qprint(f"  Found {len(jsonl_files)} session JSONL files")

    new_state  = dict(state)
    all_events = []
    total_cost = 0.0
    files_processed = 0

    for fpath in jsonl_files:
        uuid = os.path.basename(fpath).replace(".jsonl", "")

        # Skip excluded
        if uuid in exclude:
            vprint(f"  ⊘ Excluded: {uuid[:16]}")
            continue

        # Get label via uuid → session_key → label
        sk  = uuid_to_key.get(uuid, uuid)
        lbl = session_label(sk, cron_names, overrides)
        # If still an anonymous "Session XXXX", try to enrich from JSONL content
        if lbl.startswith("Session "):
            smart = smart_label_from_jsonl(fpath)
            if smart:
                lbl = smart

        last_ts = state.get(uuid)  # float or None
        events, new_max_ts = process_jsonl(fpath, last_ts, lbl, dry_run=args.dry_run)

        if events:
            files_processed += 1
            run_cost = sum(e["cost_usd"] for e in events)
            total_cost += run_cost
            if args.dry_run:
                qprint(f"  [DRY-RUN] {lbl}: {len(events)} new msgs → ${run_cost:.4f}")
            else:
                all_events.extend(events)
                qprint(f"  ✅ {lbl}: {len(events)} msgs → ${run_cost:.4f}")
        else:
            vprint(f"  💤 {lbl}: no new messages")

        if new_max_ts:
            new_state[uuid] = new_max_ts

    # Write events
    if all_events and not args.dry_run:
        write_events_locked(args.output_file, all_events)
        qprint(f"\n📊 {len(all_events)} events → {args.output_file}")
    elif not all_events and not args.dry_run:
        qprint("\n💤 No new messages found")

    if not args.dry_run:
        save_state(new_state, args.state_file)

    elapsed = time.time() - run_start
    qprint(f"\n💰 Total this run: ${total_cost:.4f}")
    qprint(f"   Files with new data: {files_processed}/{len(jsonl_files)}")
    qprint(f"   Elapsed: {elapsed:.2f}s")

    if not args.dry_run and all_events:
        print_daily_summary(args.output_file, days=3)

    try:
        with open(LAST_RUN_FILE, "w") as f:
            json.dump({
                "status":        "ok",
                "ts":            int(run_start),
                "datetime":      datetime.fromtimestamp(run_start).strftime("%Y-%m-%d %H:%M:%S"),
                "elapsed_sec":   round(elapsed, 2),
                "files_total":   len(jsonl_files),
                "files_with_new": files_processed,
                "events_logged": len(all_events),
                "total_cost_usd": round(total_cost, 6),
                "dry_run":       args.dry_run,
                "version":       "2.0",
            }, f, indent=2)
    except Exception:
        pass


# ── CSV Import Mode ───────────────────────────────────────────────────────────
def _run_csv(args, run_start):
    """
    Import costs from an Anthropic-exported CSV file.
    Expected columns: date, input_tokens, output_tokens,
                      cache_creation_input_tokens, cache_read_input_tokens, cost
    """
    if not args.csv_file:
        log_error("--mode csv requires --csv-file FILE")
        sys.exit(1)

    if not os.path.exists(args.csv_file):
        log_error(f"CSV file not found: {args.csv_file}")
        sys.exit(1)

    events = []
    rows_read = 0
    rows_skipped = 0

    try:
        with open(args.csv_file, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                rows_read += 1
                try:
                    # Parse date → midnight UTC timestamp
                    raw_date = row.get("date", "").strip()
                    dt = datetime.strptime(raw_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                    ts = int(dt.timestamp())

                    cost_usd = float(row.get("cost", 0) or 0)
                    if cost_usd == 0:
                        rows_skipped += 1
                        continue

                    input_tokens       = int(float(row.get("input_tokens", 0) or 0))
                    output_tokens      = int(float(row.get("output_tokens", 0) or 0))
                    cache_write_tokens = int(float(row.get("cache_creation_input_tokens", 0) or 0))
                    cache_read_tokens  = int(float(row.get("cache_read_input_tokens", 0) or 0))

                    event = {
                        "ts":                  ts,
                        "task":                "Anthropic CSV Import",
                        "model":               "claude",
                        "input_tokens":        input_tokens,
                        "output_tokens":       output_tokens,
                        "cache_read_tokens":   cache_read_tokens,
                        "cache_write_tokens":  cache_write_tokens,
                        "cost_usd":            round(cost_usd, 6),
                        "status":              "completed",
                        "session":             "csv-import",
                        "source":              "csv",
                    }
                    events.append(event)
                    if args.dry_run:
                        qprint(f"  [DRY-RUN] {raw_date}: ${cost_usd:.4f} ({input_tokens}in/{output_tokens}out)")
                    else:
                        vprint(f"  ✅ {raw_date}: ${cost_usd:.4f}")
                except Exception as e:
                    log_error(f"CSV row parse error: {e} — row: {row}")
                    rows_skipped += 1
    except Exception as e:
        log_error(f"CSV read error: {e}")
        sys.exit(1)

    total_cost = sum(e["cost_usd"] for e in events)
    qprint(f"\n  CSV rows: {rows_read} read, {rows_skipped} skipped, {len(events)} events")
    qprint(f"  Total cost: ${total_cost:.4f}")

    if events and not args.dry_run:
        write_events_locked(args.output_file, events)
        qprint(f"  📊 {len(events)} events → {args.output_file}")

    elapsed = time.time() - run_start
    try:
        with open(LAST_RUN_FILE, "w") as f:
            json.dump({
                "status": "ok", "ts": int(run_start), "mode": "csv",
                "events_logged": len(events),
                "total_cost_usd": round(total_cost, 6),
                "dry_run": args.dry_run, "elapsed_sec": round(elapsed, 2),
                "version": "2.0",
            }, f, indent=2)
    except Exception:
        pass


# ── OpenAI Mode ───────────────────────────────────────────────────────────────
def _run_openai(args, run_start):
    """
    Fetch costs from the OpenAI Usage API.
    GET https://api.openai.com/v1/usage?date=YYYY-MM-DD
    Response: { data: [{ n_context_tokens_total, n_generated_tokens_total, snapshot_id }] }
    """
    # Resolve API key: CLI arg → env var → config.json
    cfg = load_config(args.config_file)
    api_key = (
        args.openai_api_key
        or os.environ.get("OPENAI_API_KEY", "")
        or cfg.get("openai_api_key", "")
    )
    if not api_key:
        log_error("OpenAI API key not found. Provide via --openai-api-key, OPENAI_API_KEY env var, or config.json openai_api_key")
        sys.exit(1)

    # Resolve date
    query_date = args.date or date_cls.today().isoformat()
    try:
        datetime.strptime(query_date, "%Y-%m-%d")
    except ValueError:
        log_error(f"Invalid date format: {query_date}. Use YYYY-MM-DD")
        sys.exit(1)

    # Pricing from config or defaults
    pricing_map = cfg.get("openai_pricing", {})
    default_pricing = {
        "gpt-4o":      {"input": 2.50, "output": 10.0,  "cache_read": 1.25},
        "gpt-4o-mini": {"input": 0.15, "output": 0.60,  "cache_read": 0.075},
    }
    for model, prices in default_pricing.items():
        if model not in pricing_map:
            pricing_map[model] = prices

    url = f"https://api.openai.com/v1/usage?date={query_date}"
    qprint(f"  Fetching: {url}")

    try:
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {api_key}"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        log_error(f"OpenAI API HTTP error {e.code}: {e.reason}")
        sys.exit(1)
    except Exception as e:
        log_error(f"OpenAI API request failed: {e}")
        sys.exit(1)

    usage_entries = data.get("data", [])
    qprint(f"  Got {len(usage_entries)} usage entries for {query_date}")

    events = []
    # Parse date midnight UTC for timestamp
    dt_midnight = datetime.strptime(query_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    ts_base = int(dt_midnight.timestamp())
    total_cost = 0.0

    for entry in usage_entries:
        input_tokens  = int(entry.get("n_context_tokens_total", 0))
        output_tokens = int(entry.get("n_generated_tokens_total", 0))
        model_id      = entry.get("snapshot_id", "gpt-4o")  # e.g. "gpt-4o-2024-08-06"

        # Match pricing key (partial prefix match)
        pricing = None
        for pm_key, pm_val in pricing_map.items():
            if model_id.startswith(pm_key) or pm_key in model_id:
                pricing = pm_val
                break
        if pricing is None:
            pricing = pricing_map.get("gpt-4o", {"input": 2.50, "output": 10.0, "cache_read": 1.25})

        cost_input  = input_tokens  * pricing["input"]  / 1_000_000
        cost_output = output_tokens * pricing["output"] / 1_000_000
        cost_usd    = round(cost_input + cost_output, 6)

        if cost_usd == 0 and input_tokens == 0 and output_tokens == 0:
            continue

        total_cost += cost_usd

        event = {
            "ts":                  ts_base,
            "task":                f"OpenAI ({model_id})",
            "model":               model_id,
            "input_tokens":        input_tokens,
            "output_tokens":       output_tokens,
            "cache_read_tokens":   0,
            "cache_write_tokens":  0,
            "cost_usd":            cost_usd,
            "status":              "completed",
            "session":             "openai-usage",
            "source":              "openai-api",
        }
        events.append(event)

        if args.dry_run:
            qprint(f"  [DRY-RUN] {model_id}: {input_tokens}in/{output_tokens}out → ${cost_usd:.4f}")
        else:
            vprint(f"  ✅ {model_id}: ${cost_usd:.4f}")

    qprint(f"\n  Total: {len(events)} entries, ${total_cost:.4f} for {query_date}")

    if events and not args.dry_run:
        write_events_locked(args.output_file, events)
        qprint(f"  📊 {len(events)} events → {args.output_file}")

    elapsed = time.time() - run_start
    try:
        with open(LAST_RUN_FILE, "w") as f:
            json.dump({
                "status": "ok", "ts": int(run_start), "mode": "openai",
                "date": query_date,
                "events_logged": len(events),
                "total_cost_usd": round(total_cost, 6),
                "dry_run": args.dry_run, "elapsed_sec": round(elapsed, 2),
                "version": "2.0",
            }, f, indent=2)
    except Exception:
        pass


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log_error(f"Fatal error: {e}")
        import traceback
        log_error(traceback.format_exc())
        sys.exit(1)
