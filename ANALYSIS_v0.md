# ANALYSIS v0 — Ehrliche Ist-Analyse

## Was ist gut ✅

- **Dark Theme Fundament**: Farbpalette (#050508 Hintergrund, Cyan/Green/Red Akzente) ist kohärent und gut gewählt
- **JetBrains Mono + Inter**: Richtige Fonts für Terminal-Feeling, Google Fonts CDN geladen
- **SSE + Polling Fallback**: Technisch solide — SSE für Live-Updates, fallback auf 3s-Polling
- **Modulare JS-Struktur**: Separate `render*()` Funktionen — sauber und wartbar
- **Taxameter-Konzept**: Idee gut, Animation vorhanden (`is-running` class + tick-effect)
- **Session-Pills**: moltbook=cyan, mallorca=gold, main=green — intuitiv
- **Model-Badges**: Sonnet/Opus/Haiku — klare visuelle Differenzierung
- **Responsive Media Queries**: Mobile (600px), Tablet (900px), Ultrawide (2000px) — vorhanden
- **Chart.js Weekly Chart**: Solide eingebunden, dunkles Styling passt
- **Anomaly-Banner**: Flash-Animation, sichtbar wenn nötig
- **Grid-Hintergrund**: Subtile Bloomberg-artige Grid-Lines via CSS

## Was ist schlecht / fehlt ❌

### 🔴 KRITISCH: Status-Thresholds völlig falsch
```python
# server.py, aktuell:
def day_status(cost):
    if cost < 100:   return "green"   # 🤦 Immer grün!
    if cost < 200:   return "yellow"  # Wird nie erreicht
    return "red"
```
**Realität**: Tageskosten liegen bei ~$5, Max jemals $1.875 pro Task.
Mit $100/$200 Thresholds ist das Ampelsystem komplett nutzlos — **immer grün**.
Richtige Werte: green <$3, yellow <$10, red ≥$10 (pro Tag)

### 🔴 KRITISCH: Weekly Chart Farben genauso falsch
`if (v < 100)` → Alle Balken immer grün, kein Kontrast

### 🟡 Taxameter-Rate falsch berechnet
```javascript
taxRate = totalRunCost / Math.max(avgDur, 1);
```
Dies teilt den *bisherigen* akkumulierten Cost durch die bisherige Dauer — also die *vergangene* Durchschnittsrate. Das ist nicht die Echtzeitrate für die laufende Sekunde. Besser wäre eine modellbasierte Schätzung (Sonnet ≈ $0.003-0.015/s).

### 🟡 Taxameter im Idle-State zeigt falschen Wert
Wenn kein Task läuft, zeigt der Taxameter `taxValue = data.kpi.running_cost` — das ist der letzte bekannte Wert, nicht $0. Sollte idle bei $0.00 sein.

### 🟡 Tab-Filter (Heute/7 Tage/Monat) unvollständig implementiert
- Tab-Wechsel ändert NUR den KPI-Heute-Card-Wert
- Die Breakdown-Liste zeigt immer nur `today` data
- Das Label "Heute" ändert sich nicht entsprechend
- Verwirrend: User wechselt auf "7 Tage" aber Breakdown zeigt weiterhin Heute

### 🟡 KPI "Laufend"-Card statisch
Die Running-Kosten im zweiten KPI-Card werden nur alle 3s durch Polling/SSE aktualisiert. Der Taxameter läuft smooth, aber die KPI-Card oben friert ein. Sollte synchron mit dem Taxameter ticken.

### 🟡 Cache-Tokens völlig ignoriert
Das JSONL enthält `cache_read_tokens` (z.B. 68598 für Moltbook Cron), aber nirgends wird gecachter Token-Anteil angezeigt. Bei Anthropic ist Cache-Read deutlich billiger — relevant für die Kostenanalyse.

### 🟠 `fmtCost` logisch inkonsistent
```javascript
if (v < 10) return '$' + v.toFixed(3);  // → $0.364 statt $0.3647
if (v >= 10) return '$' + v.toFixed(2); // Fine
```
Für Werte zwischen $0.1 und $10 verliert man Präzision (3 statt 4 Dezimalstellen).

### 🟠 max-width: 2800px auf 5120-Ultrawide
Richtige Idee, aber bei 5120px bleibt 1160px Margin pro Seite leer. Der Seitenrand-Effekt macht das Dashboard auf Ultrawide nicht automatisch breiter/besser nutzbar.

### 🟠 No column header in Breakdown
Man sieht Balken + Zahlen, aber keine Spaltenüberschriften (Session / Cost / Runs).

## Was ist technisch fragwürdig

1. **`setInterval(fetchData, 3000)` + SSE gleichzeitig**: Polling und SSE laufen parallel. Das bedeutet ca. 3s Polling PLUS SSE-Push — manche Updates werden doppelt gerendert. Kein `deduplication` oder Zeitstempel-Check.

2. **`innerHTML` für alles**: Alle render-Funktionen bauen HTML via String-Template und ersetzen `innerHTML`. Das führt bei schnellen Updates zu Flackern in der Events-Liste (Scroll-Position geht verloren).

3. **Chart never destroys**: `if (!weeklyChart)` prüft ob Chart existiert, aber beim Range-Wechsel würde ein neuer Chart erstellt werden... nein warte, `weeklyChart` ist global, sollte fine sein.

4. **SSE error handler**: `es.onerror = () => { es.close(); }` — kein Reconnect-Versuch, bei SSE-Unterbrechung bleibt man auf Polling hängen.

5. **Task bar im Taxameter ist bedeutungslos**: `bar = Math.min(100, (t.cost / maxCost) * 100)` zeigt relativen Anteil, nicht Fortschritt. Für laufende Tasks irreführend.

## Ultrawide (5120x1440) — Bewertung

- `max-width: 2800px` ist sinnvoll um den Content nicht zu zerreißen ✅
- `grid-template-columns: repeat(4, 1fr)` für KPIs — würde auf Ultrawide zu sehr auseinandergezogen ⚠️
- `@media (min-width: 2000px)` fügt nur Font-Größen hinzu — keine echte Layout-Anpassung
- **Ergebnis**: Würde auf 5120px funktionieren aber sub-optimal aussehen — viel Leerraum, KPI-Cards zu weit auseinander, kein besseres Nutzung der Breite

## Mobile — Bewertung

- `@media (max-width: 600px)` vorhanden ✅
- KPI 2-Spalten, Event-Columns werden ausgeblendet
- **Problem**: Taxameter-Section hat kein Mobile-Breakpoint — `display: flex; gap: 32px` mit `min-width: 280px` wird auf kleinen Screens brechen
- Insgesamt: mittelgut, nicht getestet wirkend

## Bloomberg Terminal Feeling — Bewertung: 5/10

**Behauptet mehr als es ist.**

Bloomberg Terminal hat:
- Scharfe, rechteckige Panels ohne Radius
- KEIN Glow/Glow-Effekte
- Sehr hohe Informationsdichte
- Klare Tabellenstruktur
- Keine Farbverläufe
- Orange/Gelb als Primärfarbe

Aktuelles Dashboard hat:
- Zu viele Glowing effects (`text-shadow: 0 0 20px var(--green-glow)`) → Nicht Bloomberg, eher Cyberpunk/Neon
- Zu viele border-radius (12px, 8px) → Modernes App-Design, nicht Terminal
- Gradients in Header-Logo, Cards → Bloomberg wäre flach
- Grid-Hintergrund ist eine gute Idee aber 0.015 Opacity ist zu unsichtbar

Das Design ist **schön** aber nicht wirklich Bloomberg. Es ist ein modernes Dark Dashboard mit Terminal-Anmutung. Das ist auch ok.

## Moltbook Screenshot-Tauglichkeit: 7/10

Gut: Klare Struktur, guter Kontrast, Farbakzente sehen cool aus
Problem: Wenn Ampeln immer grün sind (falschen Thresholds) wirkt es leblos
Problem: Taxameter zeigt $0.000000 idle — sieht im Screenshot langweilig aus

---

## Priorisierung für Round 1

1. **Fix Status-Thresholds in server.py** (Kernfunktion kaputt!)
2. **Fix Weekly Chart color thresholds** (Chart ist immer einfarbig grün)
3. **Fix Taxameter idle** (soll $0.00 zeigen, nicht stale Wert)
4. **Fix Taxameter rate** (bessere Schätzung)
5. **Sync Running KPI mit Taxameter** (live ticken)
