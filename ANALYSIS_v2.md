# ANALYSIS v2 — Nach zweiter Verbesserungswelle

## Kritischer Bug behoben ✅

**BUG: Gesamtes Render brach nach jedem Poll ab**
Der Render-Fehler `TypeError: Cannot read properties of undefined (reading 'toFixed')` 
in fmtCost wurde durch `t.cost` für running events verursacht — diese haben `cost_usd` nicht `cost`.
Der Catch in `fetchData()` schluckte den Fehler still → KPI-Werte (die vor dem Fehler gerendert wurden) 
zeigten korrekte Werte, ABER Breakdown/Chart/Events blieben komplett leer.

**Fix**: Running events werden jetzt normalisiert zu `cost = t.cost_usd`, und fmtCost ist jetzt 
defensive gegen undefined/null/NaN.

## Was wurde verbessert ✅

### Design
1. **Border-radius reduziert**: 12px → 6px (cards), 8px → 4px (small elements) — spürbarer Unterschied in Terminal-Feel
2. **KPI-Cards: Left-border statt Bottom-bar** — farbiger 3px Linker Rand + dezente Top-Linie statt Footer-Bar
3. **KPI-Glow entfernt** — `text-shadow: 0 0 20px` auf Werte weg — sieht schärfer und professioneller aus
4. **Header monospace + terminal**: "KIRA // COST COCKPIT" in JetBrains Mono/Cyan statt Gradient-Logo
5. **Logo-Sub**: "AI SPEND MONITOR · robin@moltbook · PORT 8742" — mehr Info im Header

### Funktionalität
6. **Breakdown Tab-sensitiv**: "7 TAGE" / "MONAT" wechselt jetzt auch den Breakdown-Bereich
   - Server liefert `breakdown_week` und `breakdown_month`
   - Frontend zeigt die richtige Breakdown je nach aktivem Tab
7. **Scroll-Position Events**: Scroll-Position wird vor/nach Render gespeichert
8. **Chart Tooltip mit Datum**: Zeigt jetzt ISO-Datum (z.B. "2026-02-23") statt nur "Mo"
9. **Heute-Bar hervorgehoben**: Letzter Balken (heute) ist volle Sättigung, andere 6 Tage leicht gedimmt

### Technik
10. **fmtCost defensive**: Gibt '$–' für undefined/null/NaN zurück statt zu werfen
11. **Session-Pills in Events**: Jede Zeile zeigt jetzt das Session-Pill (MAIN/MOLTBOOK/MALLORCA)
12. **Taxameter Rate ≈ statt ~**: ≈$0.0027/s ist korrekter mathematisch

## Was sieht jetzt aus — Screenshot-Bewertung: 8/10

### Gut:
- Alle 4 KPI-Cards sauber und gut lesbar
- Taxameter läuft smooth mit Rate-Anzeige
- Breakdown mit farbigen Balken (grün=main, cyan=moltbook, gold=mallorca) — intuitiv
- Events mit Session-Pills und Model-Badges — professionell
- 7-Tage Chart mit korrekten Farben (grün<$3, gelb<$10, rot≥$10)
- Footer mit Pricing-Referenz

### Noch nicht perfekt:
1. **Chart "Mo" Bar ist gelb/gold** — korrekt ($5.07 > $3), aber da die anderen 4 Tage leer sind 
   sieht das etwas kahl aus. Wenig zu machen ohne Fake-Daten.
2. **KPI "LAUFEND" zeigt immer $0.xxxxxx** wenn kein Task läuft — aber formatiert als 6-Dezimalen-Zahl 
   macht keinen Sinn für $0.000000. Sollte "–" oder "$0.00" sein.
3. **Taxameter Rate zeigt "≈$0.0027/s"** — da fmtCost bei $0.0027 jetzt `toFixed(4)` = "$0.0027" gibt, 
   das ist korrekt und gut lesbar.
4. **Breakdown-Label-Breite**: 140px feste Breite für Session-Namen — bei langen Session-Namen truncated
5. **"Events: 20" im Footer** obwohl nur 8 gezeigt werden — irreführend, sollte "8/20" sein

## Bekannte Issues / Trade-offs

- Die Taxameter-Rate wird als DURCHSCHNITT der vergangenen Dauer berechnet, nicht als Echtzeit-Rate.
  Bei Anthropic's Token-by-Token billing macht das kaum einen Unterschied, aber die Rate kann sich 
  ändern je nach Task-Phase.
- Chart wird nie zerstört und neu gebaut (nur updated) — wenn man Range wechselt, behält der Chart 
  seinen Zustand. Das ist gut für Performance aber means Chart-Colors werden beim ersten Aufbau
  festgelegt und dann per `update('none')` geändert.

---

## Priorisierung für Round 3

1. **KPI "LAUFEND" idle state**: Wenn kein Task läuft, "$–" oder "$0.00" statt "$0.000000"
2. **Footer "Events: 20"** → "8 von 20" oder ähnlich
3. **Moltbook Screenshot-Optimierung**: 
   - Viewport auf 1440px setzen (Robins Nutzungskontext für Screenshots)
   - Schriftgröße für Numbers optimieren
4. **Session-Pill im Breakdown**: Show cost-per-session percentage 
5. **Klarer Leerraum-Indikator** im Chart für Tage ohne Daten (damit man sieht es sind 7 Tage)
6. **Small polish**: Abrunden von tausend Tokens besser (312k → 312K, konsistent Großbuchstabe)
