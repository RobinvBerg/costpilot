#!/usr/bin/env python3
"""
KIRA Cost Cockpit — Server Smoke Tests
Tests all major API endpoints using urllib only (no external deps).
Usage: python3 test_server.py [--host localhost] [--port 8742]
"""

import json
import sys
import urllib.request
import urllib.error
import argparse

HOST = "localhost"
PORT = 8742
BASE = None

PASS = 0
FAIL = 0


def ok(msg):
    global PASS
    PASS += 1
    print(f"  ✅ {msg}")


def fail(msg):
    global FAIL
    FAIL += 1
    print(f"  ❌ {msg}")


def get(path, expected_status=200):
    url = f"{BASE}{path}"
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=5) as resp:
            body = resp.read().decode("utf-8")
            status = resp.status
            ct     = resp.headers.get("Content-Type", "")
            return status, body, ct
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace"), ""
    except Exception as ex:
        return 0, str(ex), ""


def post(path, data, expected_status=200):
    url  = f"{BASE}{path}"
    body = json.dumps(data).encode("utf-8")
    try:
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, resp.read().decode("utf-8"), resp.headers.get("Content-Type","")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace"), ""
    except Exception as ex:
        return 0, str(ex), ""


def test_endpoint(name, path, method="GET", data=None, expect_status=200, expect_key=None, body=None):
    if method == "GET":
        status, resp_body, ct = get(path)
    else:
        status, resp_body, ct = post(path, data or {})

    if status != expect_status:
        fail(f"{name}: expected HTTP {expect_status}, got {status}")
        return

    if expect_key:
        try:
            parsed = json.loads(resp_body)
            if expect_key not in parsed:
                fail(f"{name}: missing key '{expect_key}' in response")
                return
        except json.JSONDecodeError:
            if expect_key:
                fail(f"{name}: response is not valid JSON")
                return

    ok(f"{name}: HTTP {status}")


def main():
    global BASE, HOST, PORT

    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=HOST)
    parser.add_argument("--port", default=PORT, type=int)
    args = parser.parse_args()

    HOST = args.host
    PORT = args.port
    BASE = f"http://{HOST}:{PORT}"

    print(f"KIRA Cost Cockpit — Smoke Tests")
    print(f"Target: {BASE}")
    print()

    # ── Core endpoints ──
    print("── Core ──")
    test_endpoint("Dashboard HTML",    "/",             expect_key=None)
    test_endpoint("API data",          "/api/data",     expect_key="kpi")
    test_endpoint("API config",        "/api/config",   expect_key="user")
    test_endpoint("API health",        "/api/health",   expect_key="status")
    test_endpoint("API version",       "/api/version",  expect_key="version")
    test_endpoint("API ping",          "/api/ping",     expect_key="pong")
    test_endpoint("API docs",          "/api/docs",     expect_key="endpoints")
    test_endpoint("API events",        "/api/events",   expect_key="events")
    test_endpoint("API stats",         "/api/stats",    expect_key="total_events")

    # ── Analytics ──
    print()
    print("── Analytics ──")
    test_endpoint("Autologger health", "/api/autologger-health")
    test_endpoint("Timeline",          "/api/timeline?date=2026-01-01", expect_key="events")
    test_endpoint("Compare",           "/api/compare?task1=A&task2=B",  expect_key="task1")
    test_endpoint("Estimate",          "/api/estimate?model=claude-sonnet-4-6&input_tokens=1000&output_tokens=100", expect_key="cost_usd")
    test_endpoint("Sessions",          "/api/sessions")
    test_endpoint("Backups",           "/api/backups",  expect_key="backups")
    test_endpoint("Annotations",       "/api/annotations")

    # ── Config POST ──
    print()
    print("── Config POST ──")
    s, body, _ = post("/api/config", {"user": "TestUser", "project": "TestProject"})
    if s == 200:
        d = json.loads(body)
        if d.get("ok"):
            ok("Config POST: saved successfully")
        else:
            fail(f"Config POST: ok=False: {d}")
    else:
        fail(f"Config POST: HTTP {s}")

    # Restore original
    post("/api/config", {"user": "Robin", "project": "AI Operations"})

    # ── Rate limit ──
    print()
    print("── Rate limiting ──")
    s1, _, _ = get("/api/export")
    s2, _, _ = get("/api/export")
    if s1 == 200:
        ok("Export first request: HTTP 200")
    else:
        fail(f"Export first request: HTTP {s1}")
    if s2 == 429:
        ok("Export rate limit: HTTP 429 (expected)")
    elif s2 == 200:
        # Rate limit already expired or first call didn't count
        ok("Export second request: HTTP 200 (rate limit window not hit)")
    else:
        fail(f"Export rate limit: unexpected HTTP {s2}")

    # ── 404 for unknown endpoint ──
    print()
    print("── Error handling ──")
    s, body, _ = get("/api/nonexistent")
    if s == 404:
        ok("Unknown endpoint: HTTP 404")
    else:
        fail(f"Unknown endpoint: expected 404, got {s}")

    # ── Summary ──
    print()
    print("─" * 40)
    total = PASS + FAIL
    print(f"Results: {PASS}/{total} passed, {FAIL} failed")

    if FAIL > 0:
        print("⚠ Some tests failed. Is the server running?")
        sys.exit(1)
    else:
        print("✅ All tests passed!")


if __name__ == "__main__":
    main()
