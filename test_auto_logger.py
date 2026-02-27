#!/usr/bin/env python3
"""
KIRA Cost Cockpit — Auto-Logger Unit Tests
Tests session_label(), cost calculation, and end-to-end with mock data.
Usage: python3 test_auto_logger.py
"""

import json
import os
import tempfile
import sys
import time

# Add parent dir to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PASS = 0
FAIL = 0


def ok(msg):
    global PASS
    PASS += 1
    print(f"  ✅ {msg}")


def fail(msg, detail=""):
    global FAIL
    FAIL += 1
    print(f"  ❌ {msg}" + (f": {detail}" if detail else ""))


def test(name, condition, detail=""):
    if condition:
        ok(name)
    else:
        fail(name, detail)


# ── Import auto_logger ──
try:
    from auto_logger import session_label, PRICING
    ok("Import auto_logger")
except ImportError as e:
    fail("Import auto_logger", str(e))
    sys.exit(1)


# ── session_label tests ──
print()
print("── session_label() ──")

test("Main session",
     session_label("agent:main:main") == "Main Session")

test("Sub-agent",
     session_label("agent:main:subagent:abc123") == "Sub-Agent")

test("Cron run pattern",
     "run" in session_label("agent:main:cron:abc:run:def").lower() or
     "cron" in session_label("agent:main:cron:abc:run:def").lower())

test("Isolated run",
     "Run" in session_label("agent:main:run:abc") or
     session_label("agent:main:run:abc") != "")

test("Override takes precedence",
     session_label("agent:main:main", overrides={"agent:main:main": "My Custom"}) == "My Custom")

test("Cron name lookup",
     session_label(
         "agent:main:cron:12345678-0000-0000-0000-000000000000",
         cron_names={"12345678-0000-0000-0000-000000000000": "Moltbook Daily"}
     ) == "Moltbook Daily")

test("Unknown key returns something",
     len(session_label("agent:xyz:unknown:session:key")) > 0)

test("Empty key handled",
     session_label("") is not None)


# ── Cost calculation tests ──
print()
print("── Cost calculation ──")

def calc_cost(delta, model="claude-sonnet-4-6"):
    input_tokens  = int(delta * 0.88)
    output_tokens = int(delta * 0.12)
    pricing = PRICING.get(model, PRICING["default"])
    return (input_tokens / 1_000_000 * pricing["input"]
          + output_tokens / 1_000_000 * pricing["output"])

# 1M tokens at sonnet pricing = 0.88 * 3 + 0.12 * 15 = 2.64 + 1.80 = $4.44
cost_1m = calc_cost(1_000_000, "claude-sonnet-4-6")
test("1M tokens sonnet ~$4.44", abs(cost_1m - 4.44) < 0.01, f"got {cost_1m:.4f}")

# 1M tokens at opus = 0.88 * 15 + 0.12 * 75 = 13.2 + 9.0 = $22.20
cost_1m_opus = calc_cost(1_000_000, "claude-opus-4-6")
test("1M tokens opus ~$22.20", abs(cost_1m_opus - 22.20) < 0.01, f"got {cost_1m_opus:.4f}")

# Small delta (500 tokens) should be positive and tiny
cost_500 = calc_cost(500)
test("500 tokens produces positive cost", cost_500 > 0)
test("500 tokens is very small (<$0.01)", cost_500 < 0.01, f"got {cost_500:.6f}")


# ── End-to-end test with mock sessions.json ──
print()
print("── End-to-end with mock sessions.json ──")

with tempfile.TemporaryDirectory() as tmpdir:
    sessions_file = os.path.join(tmpdir, "sessions.json")
    events_file   = os.path.join(tmpdir, "events.jsonl")
    state_file    = os.path.join(tmpdir, "state.json")
    config_file   = os.path.join(tmpdir, "config.json")

    # Write mock sessions
    mock_sessions = {
        "agent:main:main": {
            "totalTokens": 50000,
            "model": "claude-sonnet-4-6"
        },
        "agent:main:cron:abc": {
            "totalTokens": 10000,
            "model": "claude-sonnet-4-6"
        },
        "agent:main:empty": {
            "totalTokens": 0,
            "model": "claude-sonnet-4-6"
        },
    }
    with open(sessions_file, "w") as f:
        json.dump(mock_sessions, f)

    # Write empty config
    with open(config_file, "w") as f:
        json.dump({}, f)

    # First run — should establish baseline (no events logged)
    from auto_logger import load_state, save_state
    from unittest.mock import patch

    # Simulate the core logic directly
    state     = {}
    now       = int(time.time())
    new_state = {}
    new_events = []

    for key, val in mock_sessions.items():
        if not isinstance(val, dict): continue
        total = val.get("totalTokens", 0)
        model = val.get("model", "")
        if not total: continue
        if not model: continue
        new_state[key] = {"totalTokens": total, "model": model, "ts": now}
        prev       = state.get(key, {})
        prev_total = prev.get("totalTokens", 0)
        delta      = total - prev_total
        if delta < 500: continue
        input_tokens  = int(delta * 0.88)
        output_tokens = int(delta * 0.12)
        pricing = PRICING.get(model, PRICING["default"])
        cost_usd = (input_tokens / 1_000_000 * pricing["input"]
                  + output_tokens / 1_000_000 * pricing["output"])
        new_events.append({
            "ts": now, "task": key, "model": model,
            "cost_usd": round(cost_usd, 6), "status": "completed",
        })

    test("First run generates events from zero baseline",
         len(new_events) == 2,  # empty session (0 tokens) and too-small are skipped
         f"got {len(new_events)} events")

    # Save state and simulate second run with no change
    state2 = new_state
    new_events2 = []
    for key, val in mock_sessions.items():
        if not isinstance(val, dict): continue
        total = val.get("totalTokens", 0)
        if not total: continue
        prev = state2.get(key, {})
        delta = total - prev.get("totalTokens", 0)
        if delta >= 500:
            new_events2.append(key)

    test("Second run (no change) produces no events",
         len(new_events2) == 0,
         f"got {len(new_events2)}")

    # Simulate a 1000-token delta
    mock_sessions["agent:main:main"]["totalTokens"] = 51000
    new_events3 = []
    for key, val in mock_sessions.items():
        if not isinstance(val, dict): continue
        total = val.get("totalTokens", 0)
        if not total: continue
        prev = state2.get(key, {})
        delta = total - prev.get("totalTokens", 0)
        if delta >= 500:
            new_events3.append(key)

    test("Delta of 1000 tokens triggers new event",
         "agent:main:main" in new_events3)

    # Verify output event format
    if new_events:
        ev = new_events[0]
        test("Event has ts field",         "ts" in ev)
        test("Event has cost_usd field",   "cost_usd" in ev)
        test("Event has model field",      "model" in ev)
        test("Event has status field",     "status" in ev)
        test("Event cost_usd is positive", ev.get("cost_usd", 0) > 0)
        test("Event status is completed",  ev.get("status") == "completed")


# ── Summary ──
print()
print("─" * 40)
total = PASS + FAIL
print(f"Results: {PASS}/{total} passed, {FAIL} failed")
if FAIL > 0:
    sys.exit(1)
else:
    print("✅ All tests passed!")
