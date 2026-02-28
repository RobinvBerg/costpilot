# ANALYSIS — CostPilot Current State

> Last updated: 2026-02-28 | Version: v1.2

---

## Architektur

```
server.py          — Flask backend, REST API + SSE live-push, Pricing Engine, Rules Engine
dashboard.html     — Single-page frontend, Dark Terminal UI, SSE + polling fallback
auto_logger.py     — Background logger, pulls Anthropic API usage, writes cost-events.jsonl
csv_importer.py    — Import historical cost data from Anthropic CSV exports
log_cost_event.py  — CLI tool for manual event logging
config.json        — User config (API keys, session names, thresholds)
cost-events.jsonl  — Append-only event log (gitignored — can be large)
```

---

## Aktuelle Features (v1.2)

### Effizienz-Score
- Composite metric: `cost_per_ktoken × quality_score × session_weight`
- Hilft zu erkennen ob teure Sessions auch wertvoll sind

### Dual-Taxameter
- **Laufend-KPI:** Zeigt "$X.XXX" + AKTIV/Idle-Status für den aktuell laufenden Task
- **Idle-Mode:** Zeigt "● Idle — zuletzt: [Task] ([Zeit ago])"
- Rate ist historischer Durchschnitt (cost/elapsed), keine echte Echtzeit-Rate per Token-Stream

### Quality Tracking
- Pro-Task Quality-Score (0–10 skala), gespeichert in `quality-log.jsonl`
- Wird in Effizienz-Metrik einbezogen

### Rules Engine
- Konfigurierbare Regeln in `config.json`
- Automatische Alerts bei Threshold-Überschreitungen (z.B. >$3 today = gelb, >$10 = rot)
- Session-Filter: welche Sessions sollen getrackt werden

### Dashboard Features
- **KPI-Cards:** Today / Laufend / 7-Tage / Cache-Tokens
- **7-Tage Chart:** Balken farbkodiert (grün/gelb/rot) nach Tageskosten
- **Breakdown:** Session-Split mit %-Anteil für Today/Week/Month
- **Event-Log:** Letzte 20 Events mit Session- und Modell-Badge
- **SSE Live-Updates:** Grünes Badge wenn SSE connected, rotes Badge = polling fallback
- **Ultrawide Support:** `@media (min-width: 2000px)` für 5K-Displays

---

## Design

- Dark-Theme, JetBrains Mono für Zahlen
- Session-Farbkodierung: `cyan=moltbook`, `gold=mallorca`, `green=main`
- Left-Border auf KPI-Cards zeigt Severity (grün/gelb/rot)
- Terminal-Feeling: reduzierte border-radius (6px), keine Glows

---

## Bekannte Limitierungen

1. **Taxameter-Rate** ist historischer Durchschnitt (cost ÷ elapsed), nicht Token-basierte Echtzeit-Rate. Für echte Echtzeit-Rate wäre Token-Stream-Daten nötig.

2. **Chart Leertage:** Tage ohne Events zeigen fast-unsichtbare Linie statt explizitem "0". Korrekt, aber könnte expliziter sein.

3. **SSE + Polling laufen parallel:** SSE-Push überschreibt Polling (letzter Wert gewinnt). Harmlos, aber ~33% unnötige API-Calls wenn SSE aktiv.

4. **Tab-Filter (7 Tage / Monat)** ändert Breakdown und KPI-Card, aber nicht den Chart (zeigt immer letzte 7 Tage). Intuitiv, könnte aber expliziter kommuniziert werden.

5. **Mobile** ist nicht vollständig optimiert (akzeptabel für internes Tool).

---

## Nächste Schritte (offen)

- [ ] **Echte Token-basierte Rate** für Taxameter (requires Token-Stream Logging)
- [ ] **Chart Leertage** als explizite "0"-Balken markieren
- [ ] **SSE/Polling Deduplication** — polling pausieren wenn SSE aktiv
- [ ] **Bloomberg-Terminal Ästhetik** — dichtere Grid-Tables, orange Akzentfarben
- [ ] **Chart Tab-Filter Sync** — bei "Monat" auch den Chart auf 30 Tage umschalten
