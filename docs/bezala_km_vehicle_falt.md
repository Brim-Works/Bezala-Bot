# Sträcka (km), Vehicle och Antal passagerare i Bezala (C61)

## Vad vi vet

* Bezalas API (GET `/transactions/{id}`) visar inte fälten Sträcka (km), Vehicle
  eller Antal passagerare på ett kvitto. Läst 2026-11-26 på utkasten
  5594772–5594775. Det som finns: `emissions` (kg CO2e, räknas på priset) och
  `participants` (tomt).
* Bezalas API-dokumentation (help.bezala.com, "Receipts (API)") nämner inte heller
  fälten. `participants` där gäller momsraden (deltagare, t.ex. representation),
  inte passagerare.
* Liknande fält finns bara för resor/milersättning (`/api/trips`: `distance_km`,
  `vehicle`, `number_of_people`). Det är en annan sak än ett kvitto.

Därför skriver boten inte fälten än. Koden är klar
(`app/services/c60_draft.py`, `DISTANCE_FIELDS`): när fältnamnen är kända
behövs bara en liten ändring, och skrivningen går då bakom
`BEZALA_DRAFT_FILL_ENABLED` (bara utkast, bara tomma fält).

## Så här visar du vilka anrop webbformuläret gör (Chrome)

1. Öppna ett **utkast** i Bezala i Chrome: `https://app.bezala.com/receipts/<id>/edit`.
   Använd ett testutkast, inte ett som ska skickas.
2. Tryck **F12** (Mac: Cmd + Alt + I). Fönstret DevTools öppnas.
3. Klicka på fliken **Network** (Nätverk).
4. Kryssa i **Preserve log**. Skriv `transactions` i filterrutan.
5. Fyll i **Sträcka (km)**, välj **Vehicle** och skriv **Antal passagerare** i formuläret.
6. Klicka **Spara** (inte "Skicka").
7. I listan dyker en rad upp (oftast `PUT` eller `PATCH` till `.../transactions/<id>`).
   Klicka på den.
8. Välj fliken **Payload** (eller **Request**). Där syns fältnamnen och värdena
   som skickades, t.ex. `"distance": 39`.
9. Öppna också **Vehicle**-listan och se vilka val som finns. Om listan hämtas
   med ett eget anrop syns det i Network-listan.
10. Ta en skärmbild av Payload-fliken eller kopiera texten och skicka den.
    **Klipp bort** allt som ser ut som nyckel, token eller cookie
    (rader som `Authorization`, `Cookie`, `X-CSRF-Token`). Bara fältnamn och
    värden behövs.

Boten loggar aldrig in i Bezalas webbgränssnitt.
