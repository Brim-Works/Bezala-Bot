# SPEC.md — Bezala Bot Frontend-migration

> **Scope:** Byt ut nuvarande React-frontend mot designpaketets komponenter.
> **Icke-scope:** Backend, databas, integrationer, stack-byte.
> **Referens:** `design/index.html` + `screenshots/*.png` + `README.md`.

## Kontext

Bezala Bot är en befintlig produktionsapp på Railway:
- **Backend:** FastAPI + SQLAlchemy + PostgreSQL (orörd)
- **Integrationer:** Gmail OAuth2, Drive OAuth2, Claude Sonnet 4.6, Bezala API (orörda)
- **Auth:** Session-cookies server-side (orörd)
- **Frontend:** React 18 + Vite — **detta är det enda som byts ut**

Migrationen paketeras som **6 frontend-commits**. Ett commit i taget. Backend-ändringar är notes i commit-meddelanden, inte förändringar.

---

## Befintliga API-endpoints

Alla endpoints är redan byggda. Frontend ska **bara konsumera dem** — inga nya endpoints skapas i denna migration.

| Metod | Path | Användning |
|---|---|---|
| `GET` | `/api/me` | Session-kontroll, användarnamn |
| `POST` | `/login` | Sätter session-cookie |
| `POST` | `/logout` | Rensar session |
| `GET` | `/api/stats` | Dashboard-KPI:er |
| `GET` | `/api/messages?limit=<n>` | Meddelande-lista (alla vyer) |
| `POST` | `/api/messages/:id/upload-to-bezala` | Manuell överföring från Granska-vyn |
| `DELETE` | `/api/messages/errors` | Rensa felstatus-rader |
| `GET` | `/api/runs` | Körningshistorik för Logg-vyn |
| `POST` | `/api/scan` | Trigga manuell scanning |
| `GET` | `/api/settings` | Alla inställningar |
| `PUT` | `/api/settings` | Spara inställningar |

### Backend-TODOs (dokumenteras, implementeras ej här)

Följande fält kan saknas på befintliga responses. När ett commit behöver dem:
- Lägg frontend-side-fallback (visa "—" eller dölj elementet)
- Dokumentera som `BACKEND-TODO:` i commit-meddelandet
- Öppna separat issue för backend-arbete

Kända luckor:
- `GET /api/runs` saknar troligen per-stage-timing (Gmail/AI/Drive/Bezala-varaktigheter). Pipeline-tidslinjen i Logg-vyn ritar stapelbredderna proportionellt mot varaktighet — om det saknas, använd lika breda stapeldelar som fallback.
- `GET /api/messages` returnerar troligen ett enda `status`-fält i legacy-format. Frontend deriverar `file_status` + `bezala_status` via adapter (se Commit 1).
- `GET /api/stats` kan sakna `avg_confidence` eller `runs_today` — visa placeholder om saknas.

---

## Status-modell — frontend-deriverad

Backend levererar ett `status`-fält per `ProcessedMessage` (exakta värden: läs backend-enum). Frontend mappar detta till **två dimensioner** för UI:et enligt README "Status-modell".

**Adapter-exempel** (plats: `src/api/adapters.ts`):

```ts
// Legacy backend → UI
export function deriveStatuses(m: ApiMessage): { file: FileStatus; bezala: BezalaStatus } {
  switch (m.status) {
    case 'transferred':       return { file: 'saved',   bezala: 'transferred' };
    case 'saved':             return { file: 'saved',   bezala: 'pending'     };
    case 'pending':           return { file: 'saved',   bezala: 'pending'     };
    case 'bezala_error':      return { file: 'saved',   bezala: 'error'       };
    case 'error':             return { file: 'error',   bezala: 'na'          };
    case 'skipped':           return { file: 'skipped', bezala: 'na'          };
    default:                  return { file: 'error',   bezala: 'na'          };
  }
}
```

Exakt mappning finaliseras när backend-enum har verifierats. Adaptern är **enda stället** i koden där legacy-status hanteras — resten av UI:et pratar bara `file_status` + `bezala_status`.

---

## Commit 1 — Design-tokens, tema, layout-shell

**Syfte:** Bygg grunden som alla senare commits bygger på. Efter denna commit ska appen se ut som prototypen i struktur men vyerna kan fortfarande vara tomma placeholders.

### Scope

- [ ] Kopiera in **CSS-variabler** från `design/src/theme.jsx` till `src/styles/tokens.css`:
  - `--bg`, `--bg-elev`, `--surface`, `--border`, `--border-strong`
  - `--text`, `--text-dim`, `--text-muted`
  - `--accent`, `--accent-hover`, `--accent-contrast`
  - `--ok`, `--warn`, `--err`, `--info`
  - `--shadow-sm`, `--shadow-md`
  - `--radius-sm`, `--radius-md`, `--radius-lg`
- [ ] Två teman via `[data-theme="A"]` (Ljust) och `[data-theme="B"]` (Skog). Default: A.
- [ ] Ladda fonter: **IBM Plex Sans**, **IBM Plex Mono**, **Instrument Serif** (inkl. italic). Self-host eller Google Fonts — båda OK.
- [ ] Bas-typografi: `body` får Plex Sans 14px/1.5 + `--text`. `code`/`.mono` får Plex Mono. `.serif` får Instrument Serif.
- [ ] **Layout-shell**: Sidebar (vänster) + TopBar + main-content. Kopiera strukturen från `design/src/dashboard.jsx`:
  - Sidebar: logo + 4 nav-items (Översikt, Granska, Logg, Inställningar) med 1.75px linjeikoner
  - TopBar: högerställd knappgrupp (tema-växlare, språk-växlare, användarmeny)
- [ ] **Tema-växlare** och **språk-växlare** i TopBar — båda persisterar till `localStorage` (`bb_variant`, `bb_lang`)
- [ ] **i18n-skelett** med svenska (default) och engelska. Minst `nav.*` och `topbar.*` + gemensamma termer.
- [ ] **API-klient** som skickar `credentials: 'include'`, hanterar 401 → login-redirect, returnerar typade svar där TS används.
- [ ] **Adapter** `src/api/adapters.ts` som exporterar `deriveStatuses()`.
- [ ] Router-setup (om saknas): 4 routes + login + 404. Använd projektets befintliga router, troligen `react-router-dom`.

### Acceptanskriterier

- [ ] Ingen färg i någon komponent är hårdkodad — alla refererar `var(--...)`
- [ ] Tema-växling sker utan page-reload, påverkar hela UI:et, persisterar över reload
- [ ] Språk-växling uppdaterar nav-labels och topbar direkt, persisterar över reload
- [ ] Plex Sans/Mono/Instrument Serif renderas — verifiera i DevTools att rätt font-family laddas
- [ ] Sidebar-nav leder till 4 placeholders-vyer (dessa byggs ut i commits 2–5)
- [ ] `GET /api/me` anropas vid app-mount, 401 redirectar till `/login`

### Referens

- `design/src/theme.jsx` (alla variabler för båda teman)
- `screenshots/01-dashboard-top.png` (sidebar + topbar)

---

## Commit 2 — Dashboard (Översikt)

**Syfte:** Startsidan. Snabb status på systemet.

### Scope

- [ ] **4 stat-kort** högst upp: "Väntar granskning", "Auto-överförda idag", "Fel", "Total denna vecka". Första kortet har accent-kant och är klickbar länk → Granska.
  - Data: `GET /api/stats`
  - Om fält saknas i response → rendera "—"
- [ ] **Filter-tabs** ovanför tabellen: Alla / Väntar / Auto / Fel — med räknare. Klickbara, uppdaterar tabellen.
- [ ] **Sökfält** till höger om tabs (filtrerar klientsidigt på filnamn, leverantör, ämne, belopp).
- [ ] **Meddelande-tabell**:
  - Kolumner: Tid (mono) · Leverantör (logo + namn) · Ämne · Filnamn (mono, trunkerad) · Belopp (mono, höger-just.) · Konfidens (horisontell stapel) · Status (StatusCell med två staplade badges)
  - Data: `GET /api/messages?limit=50`
  - Rad-klick → öppnar pipeline-drawer (commit 6)
- [ ] **Körnings-stapel** under tabellen: 14 senaste körningarna, stapelhöjd = antal mail, röd om fel
  - Data: `GET /api/runs` (de senaste 14)
- [ ] **Manuell scan-knapp** i page-header → `POST /api/scan` med loading-state

### Komponenter (skapas i denna commit)

- `StatCard` — använder accent-kant-variant
- `VendorLogo` — bokstavs-fallback om ingen logo
- `Confidence` — horisontell stapel med färg baserad på värde
- `FileBadge` + `BezalaBadge` + `StatusCell` (staplad) — **BezalaBadge returnerar `null` för `bezala_status === 'na'`**
- `Pill` — generisk badge-bas
- `RunBars` — 14-stapel-graf

### Acceptanskriterier

- [ ] Tabellen renderar minst 20 rader med riktig data från `/api/messages`
- [ ] Status-kolumnen visar två staplade badges (ingen Bezala-badge när `na`)
- [ ] Filter-tabs uppdaterar tabellen synkront (klientsidig filtrering, inte refetch)
- [ ] Sökfält filtrerar på alla textuella kolumner
- [ ] `POST /api/scan` triggas korrekt och visar toast vid success/fail
- [ ] Tom-state när inga meddelanden finns (svensk + engelsk copy)
- [ ] Båda teman fungerar

### Referens

- `screenshots/01-dashboard-top.png`, `02-dashboard-table.png`
- `design/src/dashboard.jsx`

---

## Commit 3 — Granska-vy (Review)

**Syfte:** Manuell granskning och godkännande av meddelanden som väntar.

### Scope

- [ ] **Kö-lista** till vänster: alla meddelanden med `bezala_status === 'pending'`, sorterat äldst först.
  - Data: `GET /api/messages?limit=200`, filtrera frontend-sidigt
  - Varje rad: leverantör + belopp + filnamn + konfidens + "väntat i X dagar"
- [ ] **Detaljpanel** till höger visar vald rad:
  - **PDF-preview** — embedda från Drive via `iframe` med `src={message.drive_preview_url}` (om backend ger preview-länk) eller `<iframe src="https://drive.google.com/file/d/{fileId}/preview">`. **Mocka inte PDF:en** — använd riktig Drive-embed.
  - **AI-extraherade fält** som redigerbara inputs: Leverantör, Belopp, Moms, Datum, Konto, Beskrivning
  - **Konfidens-stapel per fält** — låg konfidens får "Granska"-pil
  - **Knapprad**: Avbryt · Spara som utkast · **Godkänn & överför** (primär)
- [ ] **Godkänn-flödet**:
  - `POST /api/messages/:id/upload-to-bezala` med redigerade fält i body (anpassa efter befintligt kontrakt — läs backend)
  - Success → toast "Överfört till Bezala", flytta till nästa rad i kön
  - Fail → toast med felmeddelande, rad byter till `bezala_status === 'error'`
- [ ] **Keyboard-shortcuts**: `J`/`K` nästa/föregående rad, `Cmd+Enter` godkänn, `Esc` avbryt

### Acceptanskriterier

- [ ] PDF-preview laddar faktisk fil från Drive (inte mock)
- [ ] Alla fält är redigerbara och skickas korrekt till backend
- [ ] Efter godkännande försvinner raden från kön (optimistic update)
- [ ] Tom-state: "Kön är tom — allt är överfört ✓" (**ingen emoji — använd linjeikon**)
- [ ] Keyboard-shortcuts funkar och dokumenteras i en liten hjälp-popover
- [ ] Båda teman fungerar

### Backend-antaganden
- `POST /api/messages/:id/upload-to-bezala` accepterar redigerade fält (verifiera i backend-koden innan implementation).
- Preview-URL finns i response från `/api/messages`. Om inte — `BACKEND-TODO: lägg till drive_preview_url`.

### Referens

- `screenshots/03-review-full.png`, `04-review-queue.png`
- `design/src/review.jsx` (som mock — PDF byts mot Drive-embed)

---

## Commit 4 — Logg-vy (Log)

**Syfte:** Historisk översikt av körningar och alla meddelanden.

### Scope

- [ ] **Körnings-lista** (vänster-kolumn): varje rad = en scanning
  - Statusprick + mono-tid + sammanfattning + varaktighet
  - Vald rad: accent-vänsterkant
  - Data: `GET /api/runs`
- [ ] **Detaljpanel** (höger):
  - **Narrativ rubrik**: Instrument Serif-datum + mening i prosa. Ex: *"Kl. 10:00 hittade botten 3 nya mail → AI extraherade alla fält → 2 auto-överförda, 1 väntar granskning."*
  - **Pipeline-tidslinje**: 4 rader (Gmail, AI, Drive, Bezala). Varje rad: ikon + label + kontextnot + mini-Gantt-stapel proportionell mot varaktighet + mono-tid
  - Om per-stage-timing saknas: rita lika breda staplar, notera `BACKEND-TODO`
  - **Processade meddelanden från denna körning** — minitabell med StatusCell
- [ ] **Tom-state** när ingen körning är vald: Instrument Serif-prompt "Välj en körning till vänster"
- [ ] **Rensa fel-knapp** högst upp → `DELETE /api/messages/errors` med confirm-dialog

### Acceptanskriterier

- [ ] Körnings-lista renderar minst 30 rader med riktig data
- [ ] Narrativ rubrik genereras från run-data (liten helper i `src/lib/runNarrative.ts`)
- [ ] Pipeline-staplar skalar proportionellt mot varaktighet (när data finns)
- [ ] Rensa-fel-flöde har confirm och visar toast
- [ ] Båda teman fungerar

### Referens

- `screenshots/05-log-detail.png`
- `design/src/log.jsx`

---

## Commit 5 — Inställningar (Settings)

**Syfte:** Konfiguration av auto-tröskel, notifieringar, integrations-status.

### Scope

- [ ] **Sektion 1: Automatisering**
  - Slider: "Auto-överför över konfidens X %" (0–100, default från `GET /api/settings`)
  - Toggle: "Skicka fel-notiser till e-post"
  - Input: notifierings-adress
- [ ] **Sektion 2: Anslutningar**
  - Tre kort: Gmail · Google Drive · Bezala
  - Varje kort: grön prick om ansluten, mail/konto som kopplad, "Koppla om"-knapp
  - Koppla-om-knapp leder till OAuth-flödet (existerande URL:er från backend)
- [ ] **Sektion 3: Utseende**
  - Tema-radio: Ljust / Skog (speglar topbar-väljaren, men bestående val)
  - Språk-radio: Svenska / Engelska
  - Täthet-radio: Bekväm / Kompakt (påverkar tabell-paddings)
- [ ] **Sparknapp** fixerad längst ner → `PUT /api/settings` med alla ändrade fält
  - Loading-state + toast vid success/fail
  - Dirty-indikator när fält ändrats men ej sparats

### Acceptanskriterier

- [ ] Alla inställningar hämtas från `/api/settings` vid vy-mount
- [ ] Sparknapp är disabled tills något ändrats
- [ ] Fel-state visar backend-felmeddelande ordagrant i toast
- [ ] Koppla-om-flöden öppnas i ny flik så backend-OAuth kan redirecta tillbaka
- [ ] Båda teman fungerar

### Referens

- `screenshots/06-settings.png`
- `design/src/settings.jsx`

---

## Commit 6 — Pipeline-drawer + polish

**Syfte:** Den djupa drilldown-vyn + sista lagret polish.

### Scope

- [ ] **Drawer från höger** — 520px bred, overlay bakom, `Esc` och overlay-klick stänger
- [ ] **Tab-rad**: Gmail · AI · Drive · Bezala — fyra flikar med tillstånds-ikon per flik (ok/warn/err)
- [ ] **Gmail-tab**: avsändare, ämne, mottaget, mail-snippet, länk "Öppna i Gmail"
- [ ] **AI-tab**: alla extraherade fält med per-fält-konfidens, AI-resonemang som collapsible-text, token-räkning (mono)
- [ ] **Drive-tab**: filnamn, mappstruktur, storlek, uppladdningstid, **inline PDF-preview** (iframe mot Drive), "Öppna i Drive"-knapp
- [ ] **Bezala-tab** — *smart, status-beroende*:
  - `bezala_status === 'pending'` → Gul banner, förslag på fält, CTA "Öppna granskning →" (navigerar till Granska med raden förvald)
  - `bezala_status === 'transferred'` → Grön banner, transaktions-ID, konto, knapp "Öppna i Bezala"
  - `bezala_status === 'error'` → Röd banner, exakt felmeddelande i `<pre>`, antal försök, CTA "Försök igen" (anropar `POST /api/messages/:id/upload-to-bezala` med lagrade fält)
  - `bezala_status === 'na'` → Grå neutral-banner som förklarar varför (ex: "Fil-uppladdning misslyckades — Bezala-steget aldrig försökt")
- [ ] **Polish-pass**:
  - Fokus-ringar överallt (accent-färg, 2px outline, 2px offset)
  - Toast-system centraliserat (`src/lib/toast.ts`) — success/warn/error-varianter
  - Loading-skeleton på alla listor (tabeller, kö) istället för spinner
  - Tom-states på alla vyer med Instrument Serif-prompt + linjeikon
  - Felbilder-boundary runt varje vy med "Ladda om"-knapp
  - Print-styling: dölj sidebar + topbar + actions, låt meddelande-tabell flöda över sidor

### Acceptanskriterier

- [ ] Drawer öppnas från alla tabeller (dashboard, logg-detalj) vid rad-klick
- [ ] Esc och overlay-klick stänger drawer, fokus går tillbaka till triggande rad
- [ ] Alla 4 bezala-status-varianter testas manuellt mot riktig data
- [ ] "Försök igen" i error-state triggar en faktisk ny upload-request
- [ ] Skeleton-states renderar istället för spinner på första load
- [ ] Keyboard-nav genom drawer-flikarna fungerar
- [ ] Båda teman fungerar på drawer

### Referens

- `screenshots/07-drawer-gmail.png` → `10-drawer-bezala.png`
- `design/src/drawer.jsx`

---

## Definition of Done (hela migrationen)

- [ ] Alla 6 commits merged, varje commit enskilt granskat
- [ ] Inga hårdkodade färger kvar (grep för `#` i CSS/inline styles)
- [ ] Båda teman renderar korrekt på samtliga vyer
- [ ] SV + EN täcker 100% av UI-strängar (inga hårdkodade svenska strängar i komponenter)
- [ ] Alla backend-TODOs listade i separat issue
- [ ] `npm run build` / `pnpm build` passerar utan nya varningar
- [ ] Smoke-test i produktionsliknande miljö (Railway preview): login → scan → granska → godkänn → logg → inställningar — hela flödet

## Vad som INTE ingår

- Backend-ändringar (utöver TODO-noteringar)
- Finska-språk
- Mobil-responsiv layout (framtida iteration)
- Ny auth-flöde
- Migration av befintlig data
- Byte av hosting / deploy-pipeline
