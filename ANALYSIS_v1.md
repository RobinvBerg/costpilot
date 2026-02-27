# ANALYSIS v1 — Nach erster Verbesserungswelle

## Was wurde verbessert ✅

### 1. Status-Thresholds komplett gefixt (KRITISCH)
**Vorher**: green < $100 → alles immer grün (nutzlos)
**Nachher**: green < $3 / yellow < $10 / red ≥ $10

**API-Test bestätigt**: Bei $5.07 Tageskosten zeigt jetzt korrekt `yellow` statt `green`.
Das Ampelsystem funktioniert jetzt WIRKLICH.

### 2. Weekly Chart Farben gefixt
**Vorher**: Alle Balken immer grün (threshold $100)
**Nachher**: Balken werden gelb/rot bei $3/$10 — passend zur Realität

### 3. Taxameter Idle-State
**Vorher**: Zeigte stale `running_cost` Wert im Idle-Modus
**Nachher**: Zeigt explizit $0.00 wenn kein Task läuft

### 4. Taxameter Rate-Berechnung
**Vorher**: `totalRunCost / avgDur` — grobe Näherung
**Nachher**: Summe aller laufenden Tasks' (cost/elapsed), mit Fallback auf $0.00045/s (Sonnet output estimate)

### 5. Running KPI-Card live tickend
**Vorher**: Nur alle 3s via SSE/Poll aktualisiert
**Nachher**: Synchron mit Taxameter-Tick (~80ms Interval) — smooth animiert

### 6. SSE Reconnect
**Vorher**: Kein Reconnect bei SSE-Fehler
**Nachher**: Retry nach 5 Sekunden

### 7. Mobile Taxameter-Fix
**Vorher**: `min-width: 280px` auf Mobile → overflow
**Nachher**: Flex-Direction column auf `<600px`, min-width entfernt

### 8. fmtCost konsistenter
**Vorher**: v < 10 → toFixed(3) (verlust bei $0.3647 → "$0.364")
**Nachher**: Saubere Staffelung: < $0.01 → 4 Dezimalen, < $10 → 2 Dezimalen

### 9. Cache-Tokens angezeigt
**Vorher**: `cache_read_tokens` aus JSONL komplett ignoriert
**Nachher**: Server aggregiert `tokens_cache`, Frontend zeigt "Cache Hit" in Token-Stats
**API-Test**: 312.016 Cache-Tokens heute sichtbar!

### 10. KPI Progress Bar
**Vorher**: Max $200 → bei $5 nur 2.5% bar (pratisch leer)
**Nachher**: Max $10 → bei $5 jetzt 50% bar — realistisch und visuell nützlich

### 11. KPI-Label beim Tab-Wechsel
**Vorher**: Label zeigt immer "Heute" auch bei 7-Tage-View
**Nachher**: Label aktualisiert sich zu "7 Tage" / "Monat"

### 12. KPI Sub-Text informativer
**Vorher**: "10 Tasks · 500k tokens"
**Nachher**: "10 Tasks · 487k in · 52k out" — aufgeteilt Input/Output

## Was noch nicht gut ist 🟡

### Breakdown-Liste zeigt immer nur Heute
Tab-Wechsel auf "7 Tage" / "Monat" ändert den Breakdown nicht.
Das ist konzeptionell unklar — wird für Runde 2 angegangen.

### Chart-Update ohne Flackern
Die Weekly Chart nutzt `weeklyChart.update('none')` ohne Animation — gut. 
Aber die Events-Liste wird bei jedem Update komplett neu gerendert → Scroll-Position verloren.

### Bloomberg-Feeling noch nicht überzeugend
Zu viel Glow-Effekte, zu viele Gradienten. Runde 2 fokussiert auf mehr Strenge.

### Kein deduplizierter Polling+SSE
Polling (3s) und SSE laufen parallel — bei SSE würde Polling redundant. Kein Harm, aber unnötige CPU.

## Potenzielle Verschlimmbessungen? ⚠️

### fmtCost Änderung: möglicherweise Präzisionsverlust bei $0.093
Alt: `$0.093` → War korrekt
Neu: `$0.09` (toFixed(2) für < $10) → **VERLUST** bei $0.093

Hmm, das ist tatsächlich schlechter für kleine Beträge! Muss in Runde 2 gefixt werden.
Besser: < $1 → toFixed(4), < $10 → toFixed(2) bleibt.

---

## Priorisierung für Round 2

1. **fmtCost Fix**: `< $1.00 → toFixed(4)` damit $0.093 korrekt bleibt
2. **Breakdown Tab-sensitiv**: Wenn "7 Tage" / "Monat", zeige entsprechende Breakdown
3. **Events-Scroll**: Scroll-Position bei Update bewahren
4. **Design: Bloomberg-Strenge**: Weniger Glow, mehr Terminal-Gefühl
5. **Chart Tooltip verbessern**: Datum anzeigen statt nur Wochentag
