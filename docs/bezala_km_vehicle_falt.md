# Sträcka (km), Vehicle och Antal passagerare i Bezala (C62)

Fälten är Bezalas "ConditionalField" på kvittot. Läst 2026-11-26.

## Läsa

`GET /api/transactions/<id>/conditional_values` ger en lista:

```json
[{"id": 266942, "expense_id": 5594772, "expense_type": "Receipt", "field_id": 2,
  "field_type": "ExpenseCategory::ConditionalField", "value": "30.4"},
 {"id": 266943, ..., "field_id": 1, "value": "Train"},
 {"id": 266932, ..., "field_id": 3, "value": "1"}]
```

`GET /api/transactions/<id>` (även med `?include=…`) visar dem inte.

## Skriva

`PUT /api/transactions/<id>` (svar 202) med

```json
{"conditional_values_attributes": [
  {"field_id": 1, "field_type": "ExpenseCategory::ConditionalField", "value": "Train",
   "expense_id": 5594772, "expense_type": "Receipt"},
  {"id": 266932, "field_id": 3, "field_type": "...", "value": "1",
   "expense_id": 5594772, "expense_type": "Receipt"}]}
```

Befintliga rader skickas med sitt `id` (annars blir det en dubblett).
`field_label`, `field_values` och `field_input_type` skickas inte.

* field_id 1 = Vehicle: `Airplane: Large ~ 900km/h`, `Airplane: Small ~ 700km/h`,
  `Bus`, `Ferry by foot (without car)`, `Ferry with car`, `Metro`, `Taxi`, `Train`, `Tram`.
* field_id 2 = Sträcka (km), text, t.ex. "30.4".
* field_id 3 = Antal passagerare, t.ex. "1" (Bezala fyller 1 som standard).

## Regler i boten (app/services/c62_conditional.py)

* Bara utkast (state "draft"), bakom `BEZALA_DRAFT_FILL_ENABLED`.
* Bara tomma fält. Mikkos egna värden skrivs aldrig över.
* Km gissas aldrig: saknas km, är det en uppskattning eller är riktningen okänd
  lämnas km och Vehicle tomma ("km saknas").
* Flyg: det längsta benet på kvittot avgör. Under 700 km (Admins "kort flyg")
  → `Airplane: Small ~ 700km/h`, annars `Airplane: Large ~ 900km/h`.
* Kan fälten inte läsas skrivs inget.
* Bezala räknar om `emissions` från Vehicle och km (5594772: 6,07 → 1,1 kg).
  Det är Bezalas siffra; Admin-raderna ändras inte.
