# Match Health 2.1 — Pipeline Transparency

*Bygg detta efter att Match Health 2.0 har använts 1-2 veckor*

## Bakgrund

Match Health 2.0 visar diagnostik för matchnings-flödet. Saknar dock synlighet i fel som händer i pipelinen själv (html_to_pdf, AI, Drive, Bezala-upload).

## Mål

Mikko ska kunna se direkt i Match Health om något kraschade tyst, utan att gräva i Railway-loggar.

## Implementation

### Backend

Utöka ProcessedMessage-schemat med:
- `pipeline_errors`: JSON-array av fel som inträffat under processing. Format: `[{stage: "html_to_pdf"|"ai_extraction"|"drive_upload"|"bezala_upload", error_type: string, message: string, timestamp: ISO, stack_trace_short: string}]`
- `last_error_at`: TIMESTAMP

Migration: idempotent ALTER TABLE.

Pipeline-flödet:
- pipeline.py i try/except per stage, fånga fel och appenda till pipeline_errors-arrayen istället för att bara logga
- Behåll befintlig logging till Railway

API:
- Utöka `/api/debug/match-health` att inkludera pipeline_errors per processed_receipt
- Utöka diagnostic_summary med pipeline_health-fält:
  - `"ok"` (inga fel)
  - `"partial_failure"` (vissa stages failade)
  - `"failed_at_<stage>"` (specifikt stage failade)

### Frontend

I expandera-vy:
- Om pipeline_errors finns: visa rödfärgad sektion "Pipeline-fel"
- Per fel: stage-namn, error_type, message, timestamp
- "Visa fullständig stack trace"-toggle om short finns

I tabell-rad: om pipeline_health != "ok", visa orange varnings-ikon.

### Tester

- ProcessedMessage med pipeline_errors visas korrekt
- Tom pipeline_errors-array → ingen sektion visas
- Multipla fel grupperas per stage
- Migration kör idempotent

## Inte i scope

- Auto-retry av failade pipeline-steg (separat feature)
- Notifieringar vid fel (kommer i v2.3)
- Bulk-retry-knapp (kommer i v2.3)
