#!/usr/bin/env python3
"""
csv_importer.py — Anthropic Console CSV → anthropic_ground_truth.json
Reads Anthropic usage CSVs and computes ground-truth hourly/daily costs.

Usage:
    python3 csv_importer.py [CSV_FILE_OR_DIR ...]
    python3 csv_importer.py  # auto-discovers *.csv in script dir

Output:
    anthropic_ground_truth.json — ground truth with daily/hourly totals

Anthropic Pricing ($/M tokens, as of 2026-02):
    claude-sonnet-4-6:
        input (no cache):  $3.00/M
        cache_write_5m:    $3.75/M
        cache_write_1h:    $3.75/M
        cache_read:        $0.30/M
        output:            $15.00/M

    claude-opus-4-6:
        input (no cache):  $15.00/M
        cache_write_5m:    $18.75/M
        cache_write_1h:    $18.75/M
        cache_read:        $1.50/M
        output:            $75.00/M

    claude-haiku-3-5 / haiku fallback:
        input (no cache):  $0.80/M
        cache_write:       $1.00/M
        cache_read:        $0.08/M
        output:            $4.00/M
"""

import csv
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
OUTPUT_FILE = os.path.join(BASE_DIR, "anthropic_ground_truth.json")

# ── Pricing table ─────────────────────────────────────────────────────────────
PRICING = {
    # model_version substring → pricing dict ($ per million tokens)
    "claude-opus-4":   {"no_cache": 15.0, "cache_write": 18.75, "cache_read": 1.50,  "output": 75.0},
    "claude-opus-3-7": {"no_cache": 15.0, "cache_write": 18.75, "cache_read": 1.50,  "output": 75.0},
    "claude-opus":     {"no_cache": 15.0, "cache_write": 18.75, "cache_read": 1.50,  "output": 75.0},
    "claude-sonnet-4": {"no_cache": 3.0,  "cache_write": 3.75,  "cache_read": 0.30,  "output": 15.0},
    "claude-sonnet-3-7":{"no_cache": 3.0, "cache_write": 3.75,  "cache_read": 0.30,  "output": 15.0},
    "claude-sonnet-3-5":{"no_cache": 3.0, "cache_write": 3.75,  "cache_read": 0.30,  "output": 15.0},
    "claude-sonnet":   {"no_cache": 3.0,  "cache_write": 3.75,  "cache_read": 0.30,  "output": 15.0},
    "claude-haiku":    {"no_cache": 0.80, "cache_write": 1.00,  "cache_read": 0.08,  "output": 4.0},
    "default":         {"no_cache": 3.0,  "cache_write": 3.75,  "cache_read": 0.30,  "output": 15.0},
}

def get_pricing(model_version: str) -> dict:
    """Return pricing dict for a model version string."""
    mv = (model_version or "").lower()
    # Match from most specific (longer) to least specific
    for key in sorted(PRICING.keys(), key=len, reverse=True):
        if key in mv:
            return PRICING[key]
    return PRICING["default"]


def calc_cost(row: dict, pricing: dict) -> float:
    """Calculate USD cost for one CSV row."""
    def tok(col):
        try:
            return int(row.get(col, 0) or 0)
        except (ValueError, TypeError):
            return 0

    no_cache     = tok("usage_input_tokens_no_cache")
    cache_w_5m   = tok("usage_input_tokens_cache_write_5m")
    cache_w_1h   = tok("usage_input_tokens_cache_write_1h")
    cache_read   = tok("usage_input_tokens_cache_read")
    output       = tok("usage_output_tokens")

    cost = (
        no_cache    * pricing["no_cache"]   +
        cache_w_5m  * pricing["cache_write"] +
        cache_w_1h  * pricing["cache_write"] +
        cache_read  * pricing["cache_read"]  +
        output      * pricing["output"]
    ) / 1_000_000

    return cost


def import_csv(fpath: str) -> list:
    """Parse one Anthropic CSV file. Returns list of hourly row dicts."""
    rows = []
    with open(fpath, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            model   = (row.get("model_version") or "").strip()
            date_s  = (row.get("usage_date_utc") or "").strip()
            if not date_s or not model:
                continue

            # Parse date string: "2026-02-21 14:00"
            try:
                dt = datetime.strptime(date_s, "%Y-%m-%d %H:%M")
            except ValueError:
                try:
                    dt = datetime.strptime(date_s, "%Y-%m-%d")
                except ValueError:
                    continue

            pricing = get_pricing(model)
            cost    = calc_cost(row, pricing)

            rows.append({
                "datetime_utc": dt.strftime("%Y-%m-%d %H:%M"),
                "date":         dt.strftime("%Y-%m-%d"),
                "hour":         dt.hour,
                "model":        model,
                "no_cache":     int(row.get("usage_input_tokens_no_cache", 0) or 0),
                "cache_w_5m":   int(row.get("usage_input_tokens_cache_write_5m", 0) or 0),
                "cache_w_1h":   int(row.get("usage_input_tokens_cache_write_1h", 0) or 0),
                "cache_read":   int(row.get("usage_input_tokens_cache_read", 0) or 0),
                "output":       int(row.get("usage_output_tokens", 0) or 0),
                "cost_usd":     round(cost, 6),
            })
    return rows


def build_ground_truth(csv_files: list) -> dict:
    """Process all CSVs and build aggregate ground truth."""
    all_rows = []
    for f in csv_files:
        rows = import_csv(f)
        all_rows.extend(rows)
        print(f"  Loaded {len(rows)} rows from {os.path.basename(f)}")

    if not all_rows:
        print("  WARNING: No rows loaded!")
        return {}

    # Aggregate by date
    daily = defaultdict(lambda: {
        "cost_usd": 0.0,
        "no_cache": 0,
        "cache_w_5m": 0,
        "cache_w_1h": 0,
        "cache_read": 0,
        "output": 0,
        "models": set(),
    })

    hourly = defaultdict(lambda: defaultdict(lambda: {
        "cost_usd": 0.0,
        "no_cache": 0,
        "cache_w_5m": 0,
        "cache_w_1h": 0,
        "cache_read": 0,
        "output": 0,
    }))

    for r in all_rows:
        d = r["date"]
        h = r["hour"]
        daily[d]["cost_usd"]   += r["cost_usd"]
        daily[d]["no_cache"]   += r["no_cache"]
        daily[d]["cache_w_5m"] += r["cache_w_5m"]
        daily[d]["cache_w_1h"] += r["cache_w_1h"]
        daily[d]["cache_read"] += r["cache_read"]
        daily[d]["output"]     += r["output"]
        daily[d]["models"].add(r["model"])

        hourly[d][h]["cost_usd"]   += r["cost_usd"]
        hourly[d][h]["no_cache"]   += r["no_cache"]
        hourly[d][h]["cache_w_5m"] += r["cache_w_5m"]
        hourly[d][h]["cache_w_1h"] += r["cache_w_1h"]
        hourly[d][h]["cache_read"] += r["cache_read"]
        hourly[d][h]["output"]     += r["output"]

    # Convert to JSON-serializable format
    daily_out = {}
    for d, v in sorted(daily.items()):
        daily_out[d] = {
            "cost_usd":     round(v["cost_usd"], 4),
            "no_cache":     v["no_cache"],
            "cache_w_5m":   v["cache_w_5m"],
            "cache_w_1h":   v["cache_w_1h"],
            "cache_read":   v["cache_read"],
            "output":       v["output"],
            "models":       sorted(v["models"]),
        }

    hourly_out = {}
    for d, hours in sorted(hourly.items()):
        hourly_out[d] = {}
        for h in range(24):
            if h in hours:
                hv = hours[h]
                hourly_out[d][str(h)] = {
                    "cost_usd":     round(hv["cost_usd"], 4),
                    "no_cache":     hv["no_cache"],
                    "cache_w_5m":   hv["cache_w_5m"],
                    "cache_w_1h":   hv["cache_w_1h"],
                    "cache_read":   hv["cache_read"],
                    "output":       hv["output"],
                }
            else:
                hourly_out[d][str(h)] = {"cost_usd": 0.0}

    return {
        "generated_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source_files":  [os.path.basename(f) for f in csv_files],
        "daily":  daily_out,
        "hourly": hourly_out,
    }


def discover_csv_files() -> list:
    """Find CSV files in common locations."""
    candidates = []
    # 1. Check media inbound directory
    media_dir = os.path.expanduser("~/.openclaw/media/inbound")
    if os.path.isdir(media_dir):
        for fn in sorted(os.listdir(media_dir)):
            if fn.endswith(".csv"):
                candidates.append(os.path.join(media_dir, fn))
    # 2. Check script directory
    for fn in sorted(os.listdir(BASE_DIR)):
        if fn.endswith(".csv") and fn != "demo-data.csv":
            candidates.append(os.path.join(BASE_DIR, fn))
    return candidates


def main():
    if len(sys.argv) > 1:
        csv_files = sys.argv[1:]
    else:
        csv_files = discover_csv_files()

    if not csv_files:
        print("Usage: python3 csv_importer.py <csv_file> [csv_file2 ...]")
        print("       OR place CSV files in ~/.openclaw/media/inbound/")
        sys.exit(1)

    print(f"Processing {len(csv_files)} CSV file(s)...")
    gt = build_ground_truth(csv_files)

    if not gt:
        print("ERROR: No data generated.")
        sys.exit(1)

    with open(OUTPUT_FILE, "w") as f:
        json.dump(gt, f, indent=2)

    print(f"\nGround truth written to: {OUTPUT_FILE}")
    print("\nDaily summary:")
    for d, v in gt.get("daily", {}).items():
        print(f"  {d}: ${v['cost_usd']:.2f}  "
              f"(no_cache={v['no_cache']:,}  cw5m={v['cache_w_5m']:,}  "
              f"cr={v['cache_read']:,}  out={v['output']:,})")

    total = sum(v["cost_usd"] for v in gt.get("daily", {}).values())
    print(f"\nTotal across all days: ${total:.2f}")


if __name__ == "__main__":
    main()
