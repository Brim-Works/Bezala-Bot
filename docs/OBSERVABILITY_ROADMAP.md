# Bezala Bot — Observability Roadmap

*Status: levande dokument, uppdateras när nya behov upptäcks*

## Vision

Ett enda verktyg där Mikko kan svara på vilken fråga som helst om Bezala Bot:s tillstånd — "varför kom inte det här kvittot med?", "vilka pipeline-fel hade vi idag?", "hur ofta misslyckas Skånetrafiken?"

## Princip

Iterativ utbyggnad. Bygg en version, använd 1-2 veckor, identifiera verklig friktion, bygg nästa version. INTE spekulera om framtida behov.

## Versioner

### v1.0 — Match Health Dashboard (mergad)

Tabell över alla missing bill_lines med verdict + score-breakdown + markdown-export.

### v2.0 — Match Health Diagnostik (mergad)

Per korttrans: utökad data med ProcessedMessages, Gmail-meddelanden, diagnostic_summary, flow-visualisering. Designsystem-fix.

### v2.1 — Pipeline Transparency

Lägg in fel-synlighet i befintlig Match Health:
- bezala_error_message per processed_receipt
- processing_errors-array per ProcessedMessage (html_to_pdf-fel, AI-fel, Drive-fel)
- pipeline_health-fält i diagnostic_summary

Svar på: "varför blev inte det här mailet en ProcessedMessage?"

Estimat: 1-2 dagar.

### v2.2 — Mail Inspector

Ny sektion med alla Gmail-mail Bezala Bot sett senaste 30 dagarna. Per mail: sender, subject, datum, attachment, pipeline-status. Filter: status, sender, datum, belopp. Drill-down till ProcessedMessage. "Force re-process"-knapp.

Svar på: "varför kommer det här kvittot inte med — det finns ju i Gmail?"

Estimat: 2-3 dagar.

### v2.3 — Pipeline Errors-vy

Dashboard för alla pipeline-fel senaste 7d. Grupperat per fel-typ. Trend-stats. Retry alla failade.

Svar på: "vad har gått snett senaste veckan?"

Estimat: 1-2 dagar.

### v2.4 — Universal Search

Sökfält över hela systemet: ProcessedMessages, Gmail, bill_lines, trips, feedback. Smart matching på belopp, datum, vendor, ord. Drill-down till entity-vy.

Svar på: "visa allt om kvittot på 73,49 EUR från 15/4"

Estimat: 2-3 dagar.

### v2.5 — Vendor Health

Per-vendor statistik. Antal kvitton, success-rate, vanligaste fel, AI-confidence-medelvärde. Vendor-alias-mapping (för Gmail-sökning). Trender över tid.

Svar på: "varför fungerar Hertz alltid dåligt? finns systematiska problem?"

Estimat: 2 dagar.

### v2.6 — Tröskel- och regel-konfigurering

Inställningar: match-tröskel (default 80, kan justeras). Per-vendor overrides. Enkla "om vendor = X, sätt kategori = Y"-regler (förlöpare till FAS 9 AI-regelgenerering).

Svar på: "Bezala Bot ska anpassa sig till mig, inte tvärtom"

Estimat: 2-3 dagar.

## Tidsplan


```
v2.0  ← mergad
   ↓ använd 1-2 veckor, samla friktion
v2.1  ← bygg om relevant
   ↓ använd 1-2 veckor
v2.2 → v2.3 → v2.4 → v2.5 → v2.6
```


Total estimerad tid: 10-15 dagars CC-arbete fördelat över 3-4 månader.

## Sannolikhet för bygge

Mikkos bedömning av vad som blir mest värdefullt baserat på nuvarande användning:

| Version | Sannolikhet att byggas |
|---------|------------------------|
| v2.1 | 95% |
| v2.2 | 80% |
| v2.3 | 30% |
| v2.4 | 50% |
| v2.5 | 70% |
| v2.6 | 90% |

Beslut tas efter varje använd version, inte i förväg.

## Tillagda idéer (backlog)

- Per-resa pipeline-flöde (FAS 11.x-rensning)
- Notifieringar vid pipeline-fel (FAS 12)
- Export av Match Health-data till CSV/Excel
- A/B-test av match-tröskel-värden
