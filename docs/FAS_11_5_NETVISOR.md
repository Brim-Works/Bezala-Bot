# FAS 11.5 — Netvisor-integration (placeholder-spec)

Status: **planerad**, inte implementerad.

> Detta är en stub-spec. Fyll i med fullständiga detaljer från
> nattprompten innan implementation startar.

## Bakgrund

FAS 11.1 etablerade `Trip`-strukturen med kvitton grupperade per resa.
FAS 11.5 ska skicka resedata till Netvisor (lönesystem) för
traktamenteberäkning, och spara tillbaka resultatet.

## Vad är förberett i FAS 11.1

`Trip`-modellen har redan följande kolumner reserverade — de är
nullable och fylls inte ännu:

```python
netvisor_trip_id     # str — Netvisors interna ID för resan
netvisor_synced_at   # datetime — när vi senast synkade till Netvisor
```

## Mål (preliminära)

1. Användaren klickar "Skicka till Netvisor" från `TripDetailDrawer`.
2. Bezala Bot bygger en payload med:
   - resedatum (start/end)
   - destination (stad/land)
   - kvittosumma per kategori (Flyg, Hotell, Mat, Taxi, Annat)
   - bifogade PDF:er via Drive-länkar (eller direkt-uppladdade)
3. POST till Netvisors API.
4. Netvisor räknar ut traktamente baserat på datum + destination
   (svenska/finska traktamenteregler).
5. Resultatet (Netvisor-ID, traktamente, ev. felmeddelande) sparas
   i `Trip.netvisor_trip_id` + `Trip.netvisor_synced_at`.

## Att klargöra innan implementation

- Vilken Netvisor-endpoint används för att skapa en resa?
- Autentisering: API-nyckel, OAuth, basic auth?
- Hur ska traktamentesumman visas i UI:t (separat fält, eller i
  total)? Räknas det av eller läggs till?
- Idempotency: hur undviker vi dubbla skickade resor (re-sync)?
- Felhantering: vilka Netvisor-fel ska blockera, vilka ska visas
  som varning?
- Räcker manuell trigger-knapp eller ska auto-sync också finnas?

## Beroenden

- FAS 11.1 (Trip-strukturen) — klar.
- Netvisor API-kontrakt — behöver bekräftas med Netvisor-dokumentation
  och en sandbox-nyckel.
- Eventuell svensk/finsk traktamentepolicy — kan vara olika beroende
  på företagets hemvist.

## UI-förslag

I `TripDetailDrawer`:
- Knapp "Skicka till Netvisor" (synlig när `status='active'` och
  `netvisor_trip_id` är NULL).
- Badge "Skickat till Netvisor — TRIP-12345 (2026-05-20)" (när
  `netvisor_trip_id` finns).
- Read-only fält "Traktamente: 1 200 SEK" (hämtat från Netvisors svar).
