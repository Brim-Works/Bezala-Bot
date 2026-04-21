# CLAUDE.md — Bezala Bot (Frontend-migration)

> Denna fil läses automatiskt av Claude Code i varje ny session.
> Läs den, följ den, avvik inte utan att fråga.

## Vad detta projekt är

**Bezala Bot** är en **befintlig, fungerande app** som automatiserar flödet: kvittomail i Gmail → AI-extraktion → Google Drive → Bezala. Backend, databas och alla externa integrationer är redan byggda och körs i produktion på Railway.

**Din uppgift är EN sak:** migrera frontend till den nya designen i `design_handoff_bezala_bot/design/`. Ingen backend ska röras. Ingen stack ska bytas. Ingen databas ska migreras.

Om du känner impulsen att "modernisera" stacken — **stopp**. Det är fel impuls.

## Befintlig stack (BEHÅLLS exakt som den är)

| Lager | Teknik | Kommentar |
|---|---|---|
| Backend | **FastAPI** (Python 3.11) | Inga ändringar |
| ORM | **SQLAlchemy** | Inga ändringar |
| DB | **PostgreSQL** | Befintliga modeller: `ProcessedMessage`, `AppSettings`, `MaintenanceTask` |
| Frontend | **React 18 + Vite** | **INTE Next.js.** Byggs om komponent för komponent |
| Auth | **Session-cookies (server-side)** | **INTE NextAuth, INTE JWT i headers** |
| Hosting | **Railway** | Inga ändringar |
| AI | Claude **Sonnet 4.6** API | Redan integrerat backend-sidigt |
| Integrationer | Gmail OAuth2, Drive OAuth2, Bezala API | Redan byggda, klara att anropa |

**Läs `package.json`, `requirements.txt` och `vite.config.*` innan du skriver kod** — där finns sanningen om vilka bibliotek som redan används.

## Absolut första steg — innan du skriver en rad kod

1. **`design_handoff_bezala_bot/README.md`** — designdokumentation (skärmar, tokens, status-modell)
2. **`design_handoff_bezala_bot/SPEC.md`** — migrationsplan i 6 frontend-commits
3. **`design_handoff_bezala_bot/design/index.html`** — öppna i webbläsare, klicka runt
4. **`design_handoff_bezala_bot/screenshots/*.png`** — pixel-referens för varje vy
5. **Lista befintliga endpoints** genom att läsa FastAPI-routerfilerna — bekräfta att de matchar SPEC:ens API-kontrakt
6. **Lista befintliga React-komponenter** som ska ersättas — så du vet scope

## Befintliga API-endpoints (använd dessa, skapa inte nya)

```
GET    /api/me                              → session-info
POST   /login                               → session-cookie sätts
POST   /logout

GET    /api/messages?limit=<n>              → lista meddelanden
POST   /api/messages/:id/upload-to-bezala   → manuell överföring
DELETE /api/messages/errors                 → rensa fel-rader

GET    /api/stats                           → dashboard-KPIer
GET    /api/runs                            → scanning-historik *
POST   /api/scan                            → trigga manuell scan

GET    /api/settings
PUT    /api/settings
```

\* `/api/runs` kan sakna per-stage-timing (Gmail/AI/Drive/Bezala-varaktigheter). Om Logg-vyn kräver det — **notera som backend-TODO i commit-meddelandet**, rör inte backend utan godkännande.

## Arbetsprocess (obligatorisk)

1. **Identifiera** vilket av de 6 frontend-commits SPEC.md din uppgift tillhör.
2. **Presentera plan** — vilka komponenter du rör, vilka API-anrop du kopplar mot, vad som är frontend-TODO vs backend-TODO.
3. **Invänta godkännande** innan du skriver kod.
4. **Implementera** — commit per delsteg, svensk eller engelsk commit-message som beskriver scope.
5. **Verifiera** mot acceptanskriterierna i SPEC.md och skärmdumparna innan du säger "klart".

Ett commit i taget. Slå inte ihop flera. Om en uppgift berör flera — fråga om prioritering.

## Frontend-regler

### Bibliotek (håll nere beroendekäns)
- Redan i stacken: **React 18**, **Vite**, troligen **TanStack Query** eller axios/fetch — använd det som finns.
- Om något saknas — **fråga innan du installerar**. Föredra små, specifika lib framför ramverk. T.ex.:
  - Ikoner: **lucide-react** (om det redan finns), annars egen set enligt designprototypen
  - Styling: behåll projektets nuvarande val (CSS Modules, plain CSS, Tailwind — vilket som), men **alla färger via CSS-variabler** (se nedan).
  - Formulär: **react-hook-form** + **zod** är bra men bara om det löser ett reellt problem.
- **Introducera inte** styled-components/emotion/MUI/Chakra/Ant Design/etc. Designen är redan specificerad i CSS — kopiera in den.

### Teman (MÅSTE fungera från första commiten)
- Båda temana (Ljust + Skog) implementeras med CSS-variabler enligt README "Design-tokens"
- Tema växlas via `[data-theme="A"]` / `[data-theme="B"]` på `<html>` eller `<body>`
- Default: Tema A (Ljust). Valet sparas i `localStorage` som `bb_variant`
- **Ingen hårdkodad färg** någonstans — alltid `var(--...)`. Detta är en hård regel.

### Fonter (MÅSTE matcha prototypen)
- **IBM Plex Sans** — UI
- **IBM Plex Mono** — belopp, filnamn, tidsstämplar, transaktions-IDs, token-siffror
- **Instrument Serif** (inkl. italic) — hero-rubriker, narrativ-datum i Logg-vyn
- Self-host via Vite-assets eller ladda från Google Fonts — val är OK, men fonter måste faktiskt laddas

### Visuella regler (icke förhandlingsbart)
- **Inga emojis** i produktions-UI.
- **1.75px linjeikoner** (stroke-baserade, `currentColor`). Inga fill-ikoner, inga blandade stilar.
- **Mono-font för belopp** — alltid, utan undantag.
- **Two-dimensional status** (`file_status` + `bezala_status`) — se README "Status-modell". Backend returnerar idag troligen ett enda `status`-fält — **derivera de två nya på frontend-sidan** via en liten helper (se `design/src/components.jsx` → `deriveStatuses`). **Rör inte backend-schemat.**
- **Drawer från höger** för Gmail/AI/Drive/Bezala-pipeline. Esc + overlay-klick stänger.

### i18n
- **Svenska är default.** Engelska som alternativ från dag 1 — struktureras som `src/i18n/{sv,en}.ts` eller likvärdigt.
- Datum/tal via `Intl.*` (`sv-FI`, `en-FI`).
- **Finska får vänta** — inte i scope för denna migration.

### State och data-fetching
- Session-auth via cookies är redan konfigurerad — `fetch` / axios måste skicka `credentials: 'include'` (eller motsvarande i din befintliga API-klient).
- Använd **TanStack Query** om det redan är i projektet. Annars bygg en liten hook-baserad wrapper — men hoppa inte direkt i `useEffect + setState`.
- 401 → redirect till login-vy.

## Vad som EXPLICIT är utanför scope

❌ Byta stack (Next.js, Prisma, NextAuth, styled-components etc.)
❌ DB-migrationer
❌ Ändra backend-endpoints (utöver att notera TODOs)
❌ Rewrite av Gmail/Drive/Bezala-integrationer
❌ Lägga till nya beroenden utan godkännande
❌ Deploy-konfiguration (Railway är som den är)
❌ Finska-språk
❌ Mobil-layout (ligger i en framtida iteration)

## Kod-regler

- **Matcha projektets befintliga stil.** Om resten av koden är i plain JS — skriv plain JS. Om TypeScript — skriv TypeScript. Ingen halvhjärtad migration.
- **Inga mockar i produktionskod.** `design/src/data.jsx` är prototyp-data — kopiera inte in det. All data kommer från `/api/*`.
- **Pixel-perfekt mot skärmdumparna.** Om något avviker → det är en bug, inte en förbättring.
- **Två teman måste fungera på varje skärm du bygger.** Testa växling innan du commitar.
- **Svar från backend kan ha legacy-fält.** Mappa i en liten adapter (`src/api/adapters.ts` eller likvärdigt) istället för att strö `status === 'saved'` i hela UI-koden.

## När du är osäker

Fråga. Gissa aldrig på:
- Vilken state-library som används
- Hur session-cookien heter eller hur login-flödet ser ut
- Exakta shape på API-responses (läs backend-koden)
- Om ett saknat fält ska komma från backend eller deriveras i frontend

## Varningssignaler — stoppa och fråga

- Du är på väg att installera ett bibliotek för styling/state/routing — fråga först
- Du ska ändra något utanför `src/` / `frontend/` — fråga först
- Du börjar skriva en migrering eller rör `requirements.txt` — fel håll
- Acceptanskriterium känns "för enkelt" — du missförstår antagligen
- Ikoner ser olika ut från prototypen — fel set

## Säkerhet

- Session-cookies ska vara HttpOnly + Secure + SameSite=Lax — bekräfta (men ändra inte utan att fråga).
- Rör inte CSRF-setup utan att förstå befintlig hantering.
- Logga **aldrig** mail-body eller PII i klartext från frontend.
- Ingen `localStorage` för känslig data — endast UI-state (tema, språk, density, view).

## Definition of Done per frontend-commit

Ett commit är klart när:
1. Alla acceptanskriterier för den commiten i SPEC.md är gröna
2. `npm run build` / `pnpm build` passerar utan fel och varningar du introducerat
3. Båda teman renderar utan brutna färger
4. UI matchar skärmdumpar på ±2px
5. Keyboard-navigation fungerar för nya interaktiva element (fokus-ringar, tab-order)
6. En kort rad i PR-beskrivningen: vad ersattes, vilka endpoints används, vilka backend-TODOs finns

---

**Den här filen är din anchor.** Om kontexten glider — öppna den igen.
