# FAS 11.2 — Kalenderintegration (placeholder-spec)

Status: **planerad**, inte implementerad.

> Detta är en stub-spec. Fyll i med fullständiga detaljer från
> nattprompten innan implementation startar.

## Bakgrund

FAS 11.1 introducerade resa-gruppering där flygbiljetter agerar anchor.
FAS 11.2 ska komplettera detta med Google Calendar-data så Bezala Bot
kan korrelera kvitton med kalenderhändelser (resor, möten,
konferenser) och därmed föreslå mer precisa resor.

## Mål (preliminära)

1. Hämta händelser från användarens primära Google Calendar (kräver
   ny OAuth-scope `calendar.readonly`).
2. Använd kalenderhändelser som ytterligare anchor för
   `trip_grouper.suggest_trips()` — t.ex. ett flerdagsmöte i Helsingfors
   blir en sannolik destination även utan flygbiljett.
3. Visa kalenderkontext i `TripDetailDrawer` så användaren ser
   "Möte: Q2 review (12-14 mar)" bredvid kvittolistan.

## Nya tabeller (förslag)

```sql
CREATE TABLE calendar_events (
  id SERIAL PRIMARY KEY,
  google_event_id VARCHAR(200) NOT NULL UNIQUE,
  calendar_id VARCHAR(255) NOT NULL,
  summary TEXT,
  description TEXT,
  location TEXT,
  start_at TIMESTAMP NOT NULL,
  end_at TIMESTAMP NOT NULL,
  fetched_at TIMESTAMP NOT NULL,
  raw_payload JSONB
);

CREATE TABLE trip_calendar_events (
  trip_id INT REFERENCES trips(id) ON DELETE CASCADE,
  calendar_event_id INT REFERENCES calendar_events(id) ON DELETE CASCADE,
  PRIMARY KEY (trip_id, calendar_event_id)
);
```

## Endpoints (förslag)

- `GET /api/calendar/events?from=YYYY-MM-DD&to=YYYY-MM-DD`
- `POST /api/calendar/sync` (manuell trigger för att hämta nya events)
- `GET /api/trips/{id}/calendar-events` (events kopplade till resan)

## Att klargöra med användaren innan implementation

- Vilken kalender ska Bezala Bot läsa från (primär, eller flera)?
- Ska privata händelser (t.ex. tandläkare) filtreras bort? Hur?
- Hur ska timezones hanteras (events i UTC, kvitton i lokal tid)?
- Räcker en daglig sync-cron, eller behövs Google Calendar push-
  notiser via webhook?

## Beroenden

- FAS 11.1 (Trip-tabeller) — krävs.
- Google OAuth scope `calendar.readonly` — kräver ny consent.
