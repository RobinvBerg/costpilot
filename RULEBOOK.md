# CostPilot Rulebook 📋

CostPilot analyzes your AI spending patterns and flags inefficiencies automatically.
These are the rules it applies — and the reasoning behind each.

---

## Rule 1: Message Batching

**Severity:** HIGH  
**Triggers when:** More than 5 message bursts in a session with fewer than 3 messages per burst on average.

**Why it matters:** Every message in a long session re-sends the entire accumulated context window. Sending five separate short questions instead of one bundled message can cost 5× as much due to repeated context overhead.

**How to fix:** Bundle 3–5 related questions into a single message per burst. Instead of rapid back-and-forth, compose your thoughts and send them together.

**Estimated savings:** Up to ~40% of per-burst message cost — scales significantly as context grows.

---

## Rule 2: Long Session (Context Bloat Risk)

**Severity:** MEDIUM  
**Triggers when:** The KIRA main session has been active for more than 4 hours **and** the cache hit rate is below 80%.

**Why it matters:** After hours of continuous chat, your context window carries the full history of every API call. Each new message re-sends all that context. This inflates token costs progressively throughout the day. Note: long sessions with a cache hit rate ≥ 80% are considered efficient — cached reads cost 10× less than fresh input tokens.

**How to fix:** Start a fresh session for unrelated tasks to reset context cost to near zero. Keep the main session for continuity-heavy work; delegate isolated tasks to sub-agents.

**Estimated savings:** ~20% of daily KIRA session spend when cache utilisation is low.

---

## Rule 3: Main Session Overuse

**Severity:** MEDIUM  
**Triggers when:** The main KIRA session accounts for more than 70% of total daily spend.

**Why it matters:** Sub-agents start with a clean, minimal context window and cost far less per token than a heavy, long-running main session. Doing everything in the main session means every task shares an ever-growing context burden.

**How to fix:** Spawn sub-agents for any task that takes more than ~10 minutes — coding tasks, research, long autonomous runs. Keep the main session lightweight and conversational.

**Estimated savings:** Up to ~30% of the excess main-session cost by delegating 20–50% of work to sub-agents.

---

## Rule 4: Low Cache Efficiency

**Severity:** MEDIUM  
**Triggers when:** The overall cache hit rate drops below 75%.

**Why it matters:** Claude caches prompt prefixes automatically. Cached tokens cost $0.30/M instead of $3.00/M — a 10× saving. When system prompts change between calls, or sessions are short and disconnected, the cache is bypassed and you pay full price for every input token.

**How to fix:** Keep system prompts stable and reuse session contexts. Avoid randomising or changing the system prompt between requests. Longer, consistent sessions naturally build better cache coverage.

**Estimated savings:** Varies by volume; achieving 85% vs 60% hit rate on 1M daily cache-read tokens saves ~$0.75/day.

---

## Rule 5: Sequential Sub-agents (Parallelise)

**Severity:** LOW  
**Triggers when:** Multiple sub-agents ran sequentially in the same clock hour when they could have run in parallel.

**Why it matters:** Independent tasks block each other unnecessarily. Running sub-agents one after another increases wall-clock time and slightly inflates shared context overhead. Parallel execution is faster and doesn't cause sub-agents to bloat each other's contexts.

**How to fix:** Run independent sub-agents concurrently. Tasks like "research X" and "generate code for Y" have no dependency and can be dispatched simultaneously.

**Estimated savings:** ~15% of average sub-agent cost per sequential session avoided, plus significant wall-clock time savings.

---

## Rule 6: Off-Peak Scheduling

**Severity:** LOW  
**Triggers when:** More than 30% of daily API events occur during the 09:00–12:00 peak hours.

**Why it matters:** While Anthropic pricing is currently flat (no time-of-day tiers), running batch jobs and cron tasks during your productive peak hours consumes rate-limit capacity and can cause your main session to slow down or hit throttling precisely when you need it most.

**How to fix:** Move cron jobs, batch processing, and non-urgent background work to nights or early mornings. Reserve peak hours for interactive, high-value work.

**Estimated savings:** No direct cost reduction today, but improved responsiveness and rate-limit headroom during productive hours.

---

## Rule 7: Tri-Model Routing (Haiku → Sonnet → Opus)

**Severity:** MEDIUM  
**Triggers when:** Always shown as a standing recommendation when sub-agent spend is non-zero.

**Why it matters:** Running every task on Claude Sonnet — regardless of complexity — is like using a sports car to fetch groceries. Haiku is ~12× cheaper than Sonnet and handles simple tasks (feed scans, formatting, checks, lookups) perfectly well. Opus excels at deep reasoning but costs more.

**How to fix:** Route by complexity:
- **Haiku** → feed scans, formatting, simple checks, short lookups
- **Sonnet** → coding, analysis, moderate reasoning (the default)
- **Opus** → multi-step reasoning, complex planning, high-stakes decisions

**Estimated savings:** Moving ~40% of sub-agent work from Sonnet to Haiku saves roughly 93% of the cost for that workload share. On $1.00/day of sub-agent spend, that's ~$0.37/day.

---

## Rule 8: Cron Results Flooding Main-Session Context

**Severity:** HIGH  
**Triggers when:** 5 or more isolated/cron sessions fired in a day **and** the main session accounts for more than 60% of total spend.

**Why it matters:** Each cron result that gets announced back into the main chat appends to the main-session context window. 35 overnight updates can add ~$4/hour in extra context cost to the first messages of the next day. The main session silently grows as a dumping ground for cron output.

**How to fix:** Configure every cron job to deliver results directly to Telegram (or your preferred channel) — not via main-session announce:

```yaml
delivery:
  mode: announce
  channel: telegram
  to: <your-chat-id>
```

This sends the result straight to your Telegram without touching the main-session context at all.

**Estimated savings:** Up to ~30% of main-session daily spend when many cron jobs are active overnight.

---

## Rule 9: Daily Gateway Restart at 07:00

**Severity:** LOW  
**Triggers when:** Always shown — no daily restart cron detected in the schedule.

**Why it matters:** Session context accumulates overnight. Without a scheduled restart, the first message of the day inherits the full prior-day context and costs accordingly. A clean daily restart means your morning context starts near zero.

**How to fix:** Add a gateway restart cron job:

```yaml
schedule:
  kind: cron
  expr: "0 7 * * *"
  payload: gateway restart
```

Memory files (`MEMORY.md`, daily notes) survive the restart because they live on disk — only the in-memory chat context is cleared.

**Estimated savings:** ~5% of daily spend from avoided context re-send on the first message of the day.

---

## Summary Table

| # | Rule ID | Severity | Category |
|---|---------|----------|----------|
| 1 | `message_batching` | 🔴 HIGH | Message efficiency |
| 2 | `long_session` | 🟡 MEDIUM | Session management |
| 3 | `main_session_overuse` | 🟡 MEDIUM | Session management |
| 4 | `low_cache_efficiency` | 🟡 MEDIUM | Token cost |
| 5 | `sequential_subagents` | 🟢 LOW | Sub-agent orchestration |
| 6 | `off_peak_scheduling` | 🟢 LOW | Scheduling |
| 7 | `tri_model_routing` | 🟡 MEDIUM | Model selection |
| 8 | `cron_announce_in_main` | 🔴 HIGH | Cron / context hygiene |
| 9 | `daily_restart` | 🟢 LOW | Session hygiene |

---

*Rules are evaluated daily from your OpenClaw usage logs. Findings are dynamically generated based on actual token counts, session durations, and cache ratios — not hardcoded thresholds.*
