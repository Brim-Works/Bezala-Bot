# CLAUDE.md — Anchor for Claude Code (FAS 5)

You are implementing **FAS 5** of Bezala Bot. FAS 4 is mergat till produktion.
Detta dokument är din **anchor** — läs först, håll i minne, följ strikt.

---

## Stack-lås (identiskt med FAS 4 — ändras INTE)

- **Backend:** FastAPI + SQLAlchemy + PostgreSQL
- **Frontend:** React 18 + Vite
- **Hosting:** Railway
- **Auth:** Session-cookies (befintlig mekanik)
- **AI:** Claude Sonnet 4.6 (befintlig Anthropic-klient)
- **Styling:** Befintliga design-tokens (tokens.css) + IBM Plex Sans/Mono + Instrument Serif
- **Teman:** Ljust (A) + Skog (B) — båda stödjas dag 1
- **Språk:** Svenska default, engelska som tillval

## Förbjudet (ingen diskussion)

- Byta stack (inte Next.js, inte Prisma, inte NextAuth, inte tRPC, inte TypeScript-migration)
- Byta ORM eller DB
- Installera nya stora bibliotek utan att fråga först — lista bibliotek som TODO för granskning
- Skriva egna design-tokens — återanvänd alltid från `tokens.css` via CSS-variabler
- Lägga till emojis i UI
- Blanda färger utanför tokens (använd `var(--accent)`, `var(--err)`, etc.)
- Avvika från 1.75px stroke-width på linjeikoner
- Introducera ny font (fortsätt med IBM Plex-familjen + Instrument Serif)

## Design-regler (FAS 4 bekräftade; gäller även FAS 5)

| Regel | Detalj |
|---|---|
| Ikoner | 20×20 viewBox, stroke-width **1.75px**, currentColor, inga fills utom `<circle>` dot-markers |
| Mono-font | `"IBM Plex Mono"` används för: belopp, datum, IDs, filnamn, hex-värden, siffror generellt |
| Serif | `"Instrument Serif"` italic, weight 300 — endast h2/h3 som sektionsrubriker |
| Sans | `"IBM Plex Sans"` — all brödtext, knappar, labels |
| Radii | 6px (knappar/inputs), 8px (små kort), 10–12px (stora kort) |
| Spacing | 4/8/12/14/18/22 — följ befintliga paddings |
| Pills | Återanvänd `<Pill kind="ok|warn|err|info|neutral|accent">` från FAS 4 |
| Confidence | Återanvänd `<Confidence value={0..1}>` från FAS 4 |
| VendorLogo | Återanvänd `<VendorLogo vendor={{name, logo, hue}}>` från FAS 4 |
| Knappar | Primary (accent-fill), Ghost (border), Danger (text-only i error-färg) |
| Toasts | Samma mekanik som FAS 4 Toast — top-right, 3–5 sek |
| Empty states | Stor serif-rubrik + dim-text + svag border-dashed container |

## FAS 5 lägger till (översikt)

Fyra nya vyer exponerade i sidebar:

1. `trash` — Papperskorg (soft-delete)
2. `rules` — Scan-regler (regelbaserad scanning)
3. `patterns` — AI-inlärning (feedback-statistik + leverantörs-mönster)
4. `cards` — Kortmatchning (reverse-match Bezala → Bezala Bot)

Plus cross-cutting:
- **Checkboxar** i Dashboard-tabell + Review-queue + Log för bulk-select (papperskorg)
- **Feedback-knappar** (tummar upp/ner) per AI-extraherat fält i Review-vyn
- **Learn-prompt-banner** i Review-vyn när användaren redigerar ett fält

## Commit-struktur (FAS 5)

Fyra commits totalt. Se `SPEC.md` för acceptanskriterier.

1. **5.1** — Soft-delete + papperskorg (backend + frontend + undo-toast)
2. **5.2** — Regelmotor (backend + frontend + templates + dry-run)
3. **5.3** — AI-inlärning (feedback-API + VendorPattern-tabell + prompt-injection + patterns-vy)
4. **5.4** — Kortmatchning (Bezala-wrapper + match-service + UI) — **innehåller minst en backend-TODO**

## Workflow — **läs → bekräfta → plan → invänta approval → bygg**

Innan du skriver kod för ANY commit:

1. Läs `CLAUDE.md` + relevant `SPEC.md`-sektion + `README.md`-delen för just den commiten.
2. Läs koden som berörs (sök t.ex. `ProcessedMessage`-modellen, befintliga endpoints i `app/main.py`, befintliga komponenter i `frontend/`).
3. **Bekräfta i ditt första svar för varje commit:**
   - Vilka 3 saker får du INTE göra? (stack-lås)
   - Vilka befintliga filer ändras?
   - Vilka nya filer skapas?
   - Finns det backend-TODOs (t.ex. Bezala-API som inte bekräftat finns)?
4. Presentera plan → invänta user-approval → koda → en PR per commit.

## Prototyp-referens

`design/` innehåller en interaktiv React-prototyp som **ska matchas visuellt och beteendemässigt**.
- `design/src/fas5-views.jsx` — alla 4 nya vyer (TrashScreen, RulesScreen, PatternsScreen, CardMatchScreen) + FeedbackInline + LearnPromptBanner
- `design/src/fas5-sidebar.jsx` — utökad sidebar
- `design/src/fas5-app.jsx` — app-shell med all state + handlers
- `design/src/fas5-data.jsx` — mock-data för alla 4 features
- `design/src/fas5-i18n.jsx` — sv/en-översättningar

Pixel-referenser i `screenshots/`.

## Öppna punkter som **du måste verifiera** innan kodning

Dessa togs ställning till i designfasen men kräver kodbas-verifiering:

- **5.1** — Finns redan en `deleted_at`-kolumn på någon tabell som mönster? Återanvänd samma konvention.
- **5.2** — Hur laddar pipelinen för närvarande filter-inställningar? Regelmotorn ska ersätta, inte dubblera, den mekaniken.
- **5.3** — Var i prompten till Claude injiceras vendor-kontext idag? VendorPattern-hints måste gå dit.
- **5.4** — **TODO:** Finns en Bezala Public API-endpoint för att lista kortrader utan bifogat kvitto? Om nej, dokumentera som blocker och fråga user innan du bygger wrappern.

Om något av ovanstående är oklart efter kodbas-läsning: **fråga först, koda sedan**.
