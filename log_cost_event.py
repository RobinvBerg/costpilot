#!/usr/bin/env python3
"""
KIRA Cost Event Logger
Logs AI task costs to cost-events.jsonl

Usage:
    python3 log_cost_event.py \
        --task "Moltbook Cron" \
        --model "sonnet" \
        --input-tokens 50000 \
        --output-tokens 4000 \
        --status "completed" \
        --duration-sec 62

    # With cache tokens:
    python3 log_cost_event.py \
        --task "Mallorca Agent" \
        --model "sonnet" \
        --input-tokens 12000 \
        --output-tokens 3500 \
        --cache-read-tokens 80000 \
        --status "completed" \
        --duration-sec 145 \
        --session "mallorca-subagent"

    # Mark a task as running (no output tokens yet):
    python3 log_cost_event.py \
        --task "Long Analysis" \
        --model "opus" \
        --input-tokens 100000 \
        --output-tokens 0 \
        --status "running" \
        --session "main"
"""

import argparse
import json
import os
import sys
import time

# Pricing per million tokens (USD)
PRICING = {
    # Model aliases → canonical name
    "aliases": {
        "sonnet":  "claude-sonnet-4-6",
        "opus":    "claude-opus-4-6",
        "haiku":   "claude-haiku-3-5",
        "claude-sonnet-4-6": "claude-sonnet-4-6",
        "claude-opus-4-6":   "claude-opus-4-6",
        "claude-haiku-3-5":  "claude-haiku-3-5",
    },
    "models": {
        "claude-sonnet-4-6": {
            "input":      3.00,   # per 1M tokens
            "output":    15.00,
            "cache_read": 0.30,
        },
        "claude-opus-4-6": {
            "input":     15.00,
            "output":    75.00,
            "cache_read": 1.50,
        },
        "claude-haiku-3-5": {
            "input":      0.80,
            "output":     4.00,
            "cache_read": 0.08,
        },
    }
}

EVENTS_FILE = os.path.join(os.path.dirname(__file__), "cost-events.jsonl")


def resolve_model(alias: str) -> str:
    return PRICING["aliases"].get(alias, alias)


def calc_cost(model: str, input_tokens: int, output_tokens: int, cache_read_tokens: int = 0) -> float:
    pricing = PRICING["models"].get(model)
    if not pricing:
        # Unknown model: estimate based on sonnet pricing
        pricing = PRICING["models"]["claude-sonnet-4-6"]
        print(f"[warn] Unknown model '{model}', using sonnet pricing as fallback", file=sys.stderr)

    cost = (
        (input_tokens / 1_000_000) * pricing["input"]
        + (output_tokens / 1_000_000) * pricing["output"]
        + (cache_read_tokens / 1_000_000) * pricing["cache_read"]
    )
    return round(cost, 6)


def infer_session(task: str) -> str:
    t = task.lower()
    if "moltbook" in t or "cron" in t:
        return "moltbook-daily"
    if "mallorca" in t or "agent" in t or "sub" in t:
        return "mallorca-subagent"
    return "main"


def main():
    parser = argparse.ArgumentParser(description="Log a KIRA cost event")
    parser.add_argument("--task",              required=True, help="Task name/description")
    parser.add_argument("--model",             required=True, help="Model alias (sonnet/opus/haiku) or full name")
    parser.add_argument("--input-tokens",      type=int, default=0)
    parser.add_argument("--output-tokens",     type=int, default=0)
    parser.add_argument("--cache-read-tokens", type=int, default=0)
    parser.add_argument("--status",            choices=["running", "completed", "failed", "cancelled"], default="completed")
    parser.add_argument("--duration-sec",      type=float, default=0)
    parser.add_argument("--session",           default=None, help="Session tag (auto-inferred if omitted)")
    parser.add_argument("--output-file",       default=EVENTS_FILE, help="Path to JSONL file")
    parser.add_argument("--anomaly-note",      default=None, help="Optional note for anomaly flagging")

    args = parser.parse_args()

    model = resolve_model(args.model)
    cost  = calc_cost(model, args.input_tokens, args.output_tokens, args.cache_read_tokens)
    session = args.session or infer_session(args.task)

    # Anomaly detection: flag if cost > expected thresholds
    anomaly = None
    task_lower = args.task.lower()
    if "moltbook" in task_lower and cost > 0.30:
        anomaly = f"COST_HIGH: ${cost:.4f} exceeds Moltbook Cron threshold ($0.30)"
    elif "mallorca" in task_lower and cost > 5.00:
        anomaly = f"COST_HIGH: ${cost:.4f} exceeds Sub-Agent threshold ($5.00)"
    if args.anomaly_note:
        anomaly = args.anomaly_note

    event = {
        "ts":               int(time.time()),
        "task":             args.task,
        "model":            model,
        "input_tokens":     args.input_tokens,
        "output_tokens":    args.output_tokens,
        "cache_read_tokens": args.cache_read_tokens,
        "cost_usd":         cost,
        "status":           args.status,
        "duration_sec":     args.duration_sec,
        "session":          session,
    }
    if anomaly:
        event["anomaly"] = anomaly

    with open(args.output_file, "a") as f:
        f.write(json.dumps(event) + "\n")

    # Human-readable output
    status_icon = {"running": "🔄", "completed": "✅", "failed": "❌", "cancelled": "⚠️"}.get(args.status, "?")
    print(f"{status_icon} Logged: {args.task} | {model} | ${cost:.4f} | {args.status}")
    if anomaly:
        print(f"⚠️  ANOMALY: {anomaly}")


if __name__ == "__main__":
    main()
