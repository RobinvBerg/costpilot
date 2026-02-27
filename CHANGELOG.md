# Changelog

All notable changes to CostPilot are documented here.

---

## [2.0.0] - 2026-02-27

### Changed
- Renamed from "KIRA Cost Cockpit" to "CostPilot"
- auto_logger: reads exact costs from session JSONL files (was: totalTokens estimate)
- Full token breakdown now tracked: input, output, cacheRead, cacheWrite
- Per-message timestamp attribution (was: session-level)

### Added
- Multi-provider input: `--mode openclaw | csv | openai`
- Basic auth via Bearer token (configurable, auto-generated on first run)
- `--no-auth` flag for local dev
- `sessions_dir` configurable via CLI and config.json
- OpenAI Usage API integration (`--mode openai`)
- Anthropic CSV import (`--mode csv`)

---

## [1.2] — 2026-02-23

300-round comprehensive enhancement build.

### Group A: Robustness & Error Handling
- Malformed JSON lines in cost-events.jsonl are skipped + counted
- File-lock on events file writes (fcntl.flock) prevents race conditions
- Server returns proper 500 JSON on unhandled exceptions
- Dashboard shows ⚠ Connection lost banner on SSE disconnect with exponential backoff retry
- auto_logger.py retries once on locked/unreadable sessions.json
- config.json validated on load; invalid fields fall back to defaults
- Defensive JS for null/undefined cost values throughout dashboard
- /api/export rate-limited to 1 request/5s per IP
- auto_logger.py logs errors to auto-logger-errors.log
- Graceful SSE fallback to polling every 10s if SSE not supported
- CORS headers on all API endpoints
- localStorage for model filter and sort preferences
- /api/data returns `error` field on failure
- "Updated X seconds ago" counter near SSE badge
- auto_logger.py skips 0-token sessions silently
- SIGTERM handling (graceful shutdown)
- auto_logger.py skips sessions with missing model field
- Cache-Control: no-cache on all API endpoints
- Debounced search input (200ms)
- Prevent duplicate event rows on SSE reconnect via ID tracking
- --dry-run flag for auto_logger.py
- /api/config POST validates required fields
- "No events match" empty state message
- auto_logger.py prints "Total cost this run: $X.XX"
- /api/health includes config_ok and events_file_writable

### Group B: Analytics & Intelligence
- 30-day rolling average line on 7-day chart
- Cost velocity (cost/min) for running tasks
- busiest_day computed server-side
- Cost per hour for last 7 days
- Efficiency trend (improving/declining/stable)
- Recurring tasks identified (≥3 appearances) with ♻ badge
- Anomaly detection (>3× own average) with ⚡ badge
- projected_month_cost in KPI tooltip
- Session count per day in weekly chart
- Top-3 frequent tasks widget
- Cache savings estimate ("Saved $X.XX via caching today")
- Week-over-week comparison badge
- All-time peak task (Hall of Fame)
- Longest session ever (Hall of Fame)
- Sort option "Slowest first" via sort UI
- Input:output ratio in expanded row view
- Tag extraction from [tag] syntax in task names
- Colored tag chips in event rows
- Daily budget remaining progress bar
- 3-day cost forecast (linear regression)
- Cost by weekday chart data
- Percentile stats (p50, p90, p99)
- Data volume warning (>100 events/day)
- Efficiency leaderboard

### Group C: Visualization
- 7-day chart hover tooltip with exact cost
- Donut chart hover tooltip with $ and %
- Hourly heatmap tooltips with exact cost
- Today's bar highlighted differently in 7-day chart
- Recurring task badges (♻) in event list
- Breakdown bars animate from 0 on load
- 7-day chart: click bar to filter events to that day
- Token flow visualization (input vs output)
- Heatmap: cool-to-hot color scale
- Donut chart: click slice to filter by model
- Progress ring around Taxameter showing budget %
- Average cost reference line on weekly chart
- Today's cost sparkline
- Hall of Fame row for all-time records
- Event row fade-in animation for SSE new events
- Accessibility: aria-label on all canvas elements
- Chart Y-axis formatted as "$X"

### Group D: Data Management
- /api/archive — move events >30 days to archive
- /api/import — POST JSONL with dedup + validation
- /api/clear — DELETE with CONFIRM token
- /api/backups — list available backups
- POST /api/restore — restore from backup
- auto_logger.py appends session_label for traceability
- Deduplication: skip events with identical ts+task+cost
- /api/events?from=TS&to=TS date range filtering
- Date range picker in dashboard
- Automatic daily backup at midnight
- /api/stats endpoint
- Tag-based filtering via /api/data?tag=X
- auto_logger.py PID file to prevent multiple instances
- /api/events returns paginated results
- Event id field (MD5 hash) for stable references
- total_cost_all_time and total_events_all_time in /api/data
- File mtime caching in load_events()
- /api/merge documented (dedup via import)
- Export format options: CSV/JSON/Markdown

### Group E: Configuration & Customization
- theme, date_format, default_sort, default_filter in config
- show_sessions, compact_default, max_events_display
- hide_zero_cost, group_by_task, show_token_counts
- cost_precision (2/4/6 decimal places)
- model_aliases for display names
- session_label_overrides
- exclude_sessions list
- webhook_url for daily summary
- notify_on_threshold for budget alerts
- weekly_goal_usd with progress bar
- dashboard_title custom title
- retention_days for auto-archive
- alert_levels for multi-level alerts
- All config options in ⚙ modal
- Config changes immediately reflected via state cache invalidation

### Group F: UX & Accessibility
- Focus rings on all interactive elements
- ARIA labels on charts
- High contrast mode toggle
- Font size toggle (Normal/Large), saved to localStorage
- All modals closeable with Escape key
- Loading skeleton for events list
- "Jump to top" button when scrolled
- Keyboard shortcuts panel (? key)
- Smooth scroll behavior
- Page title shows current cost
- Emoji favicon (💰)
- Double-click row to copy JSON
- Mark as reviewed (localStorage), shown at reduced opacity
- Pin events (gold border, persisted in localStorage)
- Quick filter chips (Today/This Week/Running/Anomalies)
- Event row pulse animation on SSE new event
- Arrow key navigation through event list
- Context menu (right-click): Copy, Export, Mark reviewed, Pin, Annotate, Rename
- "Scroll to latest" button
- Compact mode persisted in localStorage
- Mobile bottom navigation bar
- role="status" on Taxameter for screen readers
- Focus mode (F key)
- tab key navigation on KPI cards
- "No events match" empty state

### Group G: Developer Experience
- README.md: Quick Start, API reference, architecture, FAQ, deployment
- CHANGELOG.md
- CONTRIBUTING.md
- --port, --host, --data-file, --config-file for server.py
- --output-file, --config-file, --sessions-file, --verbose, --quiet for auto_logger.py
- install.sh and uninstall.sh
- Makefile with start/stop/install/test/clean
- test_server.py smoke tests
- test_auto_logger.py unit tests
- --json-log structured logging
- auto-logger-last-run.json execution summary
- ETag support for /api/data
- /api/docs endpoint with full schema
- .gitignore
- config.example.json
- Dockerfile + docker-compose.yml
- meta description + Open Graph tags
- Optional basic auth
- --export-csv flag
- /api/sessions endpoint
- LICENSE (MIT)
- X-Request-ID header on all responses
- Security headers (X-Frame-Options, X-Content-Type-Options)

### Group H: Advanced Features
- Multi-level budget alerts (warn/critical)
- Cost forecast widget ("At this rate, you'll spend $X")
- Linear regression for 3-day cost forecast
- /api/compare?task1=X&task2=Y
- /api/timeline?date=YYYY-MM-DD
- /api/report?format=markdown weekly summary
- Cost breakdown by hour table
- Efficiency leaderboard
- Weekly goal progress bar
- Browser notifications on anomaly/threshold
- Tags panel with aggregate cost by tag
- Task name editor (rename via PATCH endpoint)
- Annotations system (add notes to events)
- Cost calculator modal
- Currency conversion (rate from config)
- /api/estimate for cost estimation

### Group I: Final Polish
- gzip compression for large API responses
- 1-second state cache in build_state()
- preload critical fonts
- mobile viewport meta tag (already present)
- Delta SSE compression (server-side caching)
- PWA manifest link
- /api/ping endpoint
- schema_version in /api/data
- slow query detection (>500ms warning)
- Performance marks in debug mode
- Events file checksum validation
- Security headers on all responses
- Request logging to auto-logger-last-run.json
- Version v1.2, build date updated
- All debug console.log removed from production paths

---

## [1.1] — 2026-02-23

First enhancement build (30 rounds):
- R1: Sortable events table
- R2: Model filter dropdown
- R3: Category filter via breakdown click
- R4: Keyboard shortcuts (R, E, Escape)
- R5: KPI tooltips
- R6: Expandable event rows
- R7: Model split donut chart
- R8: Sticky events header
- R9: Copy cost to clipboard
- R10: Demo mode badge
- R11: Config modal (user/project/threshold/refresh)
- R12: Category color coding from config
- R13: Efficiency badge (output ratio)
- R14: Alert threshold banner
- R15: Hourly cost heatmap
- R16: Auto-logger health indicator
- R17: Autologger health age coloring
- R18: Compact event view toggle
- R19: Yesterday cost comparison
- R20: Duration formatting
- R21: Peak task callout
- R22: Running total column
- R23: Pause/resume SSE
- R24: Darker theme toggle
- R25: Search filter
- R26: Trend arrow on avg cost
- R27: Print styles
- R28: Footer stats (avg duration, peak hour)
- R29: Task name truncation with tooltip
- R30: Breakdown week/month views

---

## [1.0] — 2026-02-23

Initial release:
- Bloomberg Terminal aesthetic
- Live SSE updates
- Cost aggregation from JSONL events
- KPI cards (today/running/avg/projection)
- Live Taxameter
- Daily breakdown bars
- 7-day bar chart
- Auto-logger for OpenClaw session tracking
