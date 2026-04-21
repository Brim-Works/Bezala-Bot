# Handoff: Bezala Bot

## Översikt

**Bezala Bot** är en webbapplikation som automatiserar flödet från kvittomail i Gmail till bokförda utgifter i Bezala, med AI-baserad fältextraktion och en människa-i-loopen granskningsvy för låg-konfidensfall.

Botten läser inkommande mail, laddar ner PDF-bilagor, använder Claude Sonnet 4.6 för att extrahera belopp/moms/leverantör/kategori, döper om filen enligt mall, laddar upp till Google Drive och skapar en transaktion i Bezala. Utgifter där AI:n är osäker hamnar i en granskningskö för manuell bekräftelse.

## Scope — detta är en frontend-migration, inte en greenfield-build

Bezala Bot är en **befintlig produktionsapp** på Railway. Backend, databas och alla externa integrationer är redan byggda och fungerar. Uppgiften är att **byta ut den nuvarande React-frontend-koden** mot designen i `design/` — ingenting annat.

### Stack som BEHÅLLS (rör inte)

| Lager | Teknik |
|---|---|
| Backend | **FastAPI** (Python 3.11) + SQLAlchemy |
| Databas | **PostgreSQL** — befintliga modeller: `ProcessedMessage`, `AppSettings`, `MaintenanceTask` |
| Frontend-ramverk | **React 18 + Vite** (INTE Next.js) |
| Auth | **Session-cookies** server-side (INTE NextAuth, INTE JWT) |
| Hosting | **Railway** |
| Integrationer | Gmail OAuth2, Drive OAuth2, Claude Sonnet 4.6 API, Bezala API — alla redan byggda |

### Vad som SKA göras

Frontend-migration i 6 commits — se `SPEC.md`. Design-tokens → dashboard → granska → logg → inställningar → drawer + polish.

### Vad som INTE SKA göras

- Ingen stack-byte (Next.js, Prisma, NextAuth, styled-components, etc.)
- Inga DB-migrationer
- Inga backend-endpoint-ändringar (luckor dokumenteras som `BACKEND-TODO:`)
- Inga nya beroenden utan godkännande

### Om designfilerna

Filerna i `design/` är **designreferenser skapade i HTML** — en funktionell prototyp som visar avsett utseende och beteende, inte produktionskod att kopiera rakt av. Kopiera CSS-variabler, komponentstruktur och beteende in i den befintliga Vite-kodbasen enligt dess etablerade mönster. Tänk på prototypen som en högupplöst "Figma-fil i HTML-format".

Prototypen är byggd i vanilj-React via Babel-standalone med inline-styles. Detta är medvetet — i produktion följer du projektets befintliga stilval (CSS Modules / plain CSS / Tailwind — vad som än används idag).

## Fidelitet

**Hög fidelitet (hifi).** Prototypen innehåller:
- Exakta färger (två kompletta teman: Ljust och Skog)
- Komplett typografisk skala (IBM Plex Sans/Mono, Instrument Serif)
- Alla hover-, active- och focus-tillstånd
- Riktiga interaktiva element (drawer-navigering, formulär, filtrering)
- Tre olika skärmlayouter (Översikt, Granska, Logg/Kommandocenter)
- Animationer och övergångar

Återskapa UI:t **pixel-perfekt** men använd din kodbases befintliga bibliotek (shadcn/ui, Radix, Headless UI, etc.) där det finns etablerade motsvarigheter.

---

## Skärmar / Vyer

### 1. Översikt (Dashboard)
**Syfte:** Snabb status på hela systemet — vad har processats, vad väntar, vad har felat.

**Layout:**
- Två kolumner: sidofält (210px) + huvudinnehåll (fill, max-width ~1280px)
- Huvudinnehåll scrollar; sidofält och toppbar är stationära
- Vertikalt: hero-strip → 4-kolumns statistikgrid → körningslogg → rader-tabell → scanning-stapel

**Komponenter:**
- **Hero-strip:** stor rubrik med Instrument Serif + `<em>`-emfas (*"Bezala Bot **automates** receipts"*), subtitel höger
- **Stat-kort (4 st):** ljus bakgrund, tunn kant, liten label uppe, stor siffra mitten, subtitel botten. Första kortet ("Väntar") har accentkant och klickbar länk
- **Filter-tabs:** flikar i rad — Alla / Väntar / Auto / Fel — med räknare. Sökfält till höger
- **Tabell:** tid, leverantör (med logo-badge), ämne, filnamn (mono-font), belopp (mono, höger-justerat), konfidens (liten horisontell stapel), **split-status** (två staplade badges: fil-status ovanpå, Bezala-status under)
- **Körnings-stapel:** horisontell stapelgraf, 14 senaste körningarna, röd om fel, dimmad om 0 mail

### 2. Granska innan överföring (Review)
**Syfte:** Bekräfta AI:ns extraktion för låg-konfidens-rader innan överföring.

**Layout:**
- 3-kolumns grid: kö (340px) | PDF-preview (1fr) | formulär (1fr)
- Höjd: `calc(100vh - 180px)`, min 560px
- Toppbar: rubrik + progress (`N/M`) + föregående/nästa + "Godkänn alla"

**Komponenter:**
- **Kö:** scrollbar lista, vald rad har accent-bakgrund, visar leverantör-logo + datum + konfidens + belopp
- **PDF-preview:** pappersliknande mockup på gråbeige bakgrund med dither-pattern. Header med filnamn + Gmail-pill + download-ikon
- **Formulär:** fält grupperade i rader (vendor+date, amount+currency+vatRate, category+project), editerade fält får gul kant + mono-label. Footer: avvisa / hoppa över / **Godkänn** (primär, grön/cream)
- **Edit-indikator:** *"N fält manuellt redigerade"* visas bara om N > 0

**Tangentbordsnavigation:** J/K eller pilar för att byta rad, Enter godkänner.

### 3. Logg / Kommandocenter (Log)
**Syfte:** Full spårbarhet — vad gjorde botten, när, hur länge tog varje steg, vad kostade det.

**Layout:**
- KPI-strip: 4 stat-kort (körningar 24h, auto-rate, AI-kostnad, fel)
- Split nedan: körningslista (360px) + detaljpanel (1fr)
- Under 900px viewport: stackar vertikalt

**Komponenter:**
- **Körningsrad:** färgad statusprick + tid (mono) + sammanfattning + varaktighet. Vald rad har accent-vänsterkant
- **Narrativ rubrik (detaljpanel):** Instrument Serif-datum + mening i prosa som berättar vad som hände: *"Kl. 10:00 hittade botten 3 nya mail → AI extraherade alla fält → 2 auto-överförda till Bezala · 1 väntar granskning"*
- **Pipeline-tidslinje:** 4 rader (Gmail/AI/Drive/Bezala), varje med ikon + label + kontextnot + mini-Gantt-stapel proportionell mot varaktighet + mono-tid till höger
- **Token-statistik:** nedre raden i pipeline-kortet med input/output-tokens och kostnad i euro
- **Meddelanden-tabell:** rader för denna körning, klickbara — öppnar pipeline-drawer för det meddelandet

### 4. Inställningar (Settings)
**Syfte:** Anslut Gmail, välj Drive-mapp, Bezala-token, justera auto-tröskel.

**Layout:** Enkel vertikal lista med sektionsrubriker. Ingen komplexitet värd att detaljera här — följ din kodbases formulär-mönster.

---

## Pipeline-drawer (systemövergripande komponent)

Detta är den viktigaste interaktionen i appen. När användaren har valt en rad (i Översikt eller Granska) visar **toppbaren** och **sidofältet** en pipeline `Gmail → AI → Drive → Bezala` där varje steg är klickbart.

### Beteende
- **Inaktivt läge:** Dimmad opacity 0.55, cursor default, tooltip *"Välj en rad först"*
- **Aktivt läge:** Full opacity, pointer-cursor
- Klick öppnar en **drawer** från höger (520px bred, full höjd)
- Overlay 35% svart bakom, klick eller Esc stänger

### Fyra drawer-varianter

**Gmail-drawer:** Full mailvy — från/till/ämne, tidsstämpel, etiketter, bilagor, brödtext i pre-formaterat block. Knapp "Öppna i Gmail" (stannar in-app som preview).

**AI-drawer:** Modell-banner (Claude Haiku 4.5 + konfidens), lista med extraherade fält som key/value, "Resonemang"-block med AI:ns förklaring, token-statistik längst ner.

**Drive-drawer:** Filnamn, mappstruktur (`/Kvitton/2026/Q2`), storlek, uppladdningstid, inline PDF-preview, knappar för nedladdning och "Öppna i Drive".

**Bezala-drawer (smart, status-beroende):**
- `bezala_status === 'pending'` → Gul varningsbanner, förslag på fält, **primär CTA "Öppna granskning →"** som navigerar till Granska-vyn med raden förvald
- `bezala_status === 'transferred'` → Grön "Överfört"-banner, Bezala-kvitto (transaktions-ID, bokföringskonto, godkänd av), knappar "Öppna i Bezala" / "Exportera"
- `bezala_status === 'error'` → Röd banner, exakt felmeddelande i `<pre>` (ex: `422 Unprocessable Entity · "vat_rate" does not match...`), antal försök, **primär CTA "Åtgärda och försök igen"**
- `bezala_status === 'na'` (nollsätts när `file_status` är `error` eller `skipped`) → Grå neutral-banner som förklarar *varför* det aldrig nådde Bezala

### Viktig UX-detalj
"Öppna i Gmail"-knappen i Granska-vyn och Gmail-pilen i pipelinen är **olika saker**:
- **Pipeline-pilen** → drawer, in-app preview (behåller fokus)
- **"Öppna i Gmail ↗"** → öppnar extern Gmail-URL i ny flik (`https://mail.google.com/...`)

---

## Interaktioner & beteende

### Navigation
- Sidofält-ikoner växlar vy (Översikt / Granska / Logg / Inställningar)
- Vy-valet sparas i `localStorage` som `bb_view`
- URL-routing **är inte implementerad** i prototypen — lägg till i produktion (Next.js App Router eller motsvarande)

### Rad-val (shared state)
- En `selectedId` lever i app-nivå
- Ändras av klick i Översikts-tabellen och av navigering i Granska-kön
- Pipeline-drawern agerar alltid på det valda ID:t, oavsett vy

### Scan-knapp
- Visar spinner under 1.4s (mockad)
- Toast vid slutförande ("Scanning klar — inga nya kvitton")
- I produktion: `POST /api/scan` → 202 → polla `/api/runs/:id`

### Tangentbord
- Esc stänger drawer
- Enter i Granska godkänner raden
- J/K / piltangenter i Granska byter aktiv rad

### Animationer
- Drawer: `fade .15s ease` på overlay, slide-in på aselement (CSS transition eller Framer Motion)
- Pipeline-stapel i logg: `width transition 0.3s ease` när man byter körning
- Toast: 3s visible, fade ut

### Responsivt beteende
- Prototypen är desktop-först (≥1280px optimal)
- `Log-split` stackar vertikalt under 900px
- **Mobil-layout är inte designad** — se SPEC.md Projekt 6 för krav

---

## State-hantering

### Globalt (App-nivå)
- `variant` ('A' | 'B') — tema, sparas i localStorage
- `lang` ('sv' | 'en') — språk, sparas i localStorage
- `density` ('cozy' | 'compact') — radhöjd, sparas i localStorage
- `view` ('dashboard' | 'review' | 'log' | 'settings') — aktiv vy
- `selectedId` (number | null) — rad som pipeline refererar till
- `drawerStep` ('gmail' | 'ai' | 'drive' | 'bezala' | null)
- `messages` (Message[]) — alla processade mail
- `scanning` (bool), `toast` (string | null)

### Lokalt (Granska)
- `activeId` är nu hämtat från globalt `selectedId`
- `form` — redigerat fält-objekt
- `editedKeys` (Set) — vilka fält användaren ändrat

### Data-fetching (ska implementeras)
Alla nuvarande data kommer från `window.MOCK_MESSAGES` och `window.MOCK_RUNS` i `src/data.jsx`. Byt ut mot API-anrop enligt SPEC.md Projekt 3.

---

## Design-tokens

### Tema A — "Bezala Modern Light" (default)
```
--bg:             #f7f7f4  (off-white)
--bg-2:           #efefe9
--surface:        #ffffff
--surface-2:      #f3f3ee
--surface-3:      #e7e7df
--border:         #e4e3db
--border-strong:  #cfcec2
--text:           #111412
--text-2:         #4b524c
--muted:          #8a8f88
--accent:         oklch(48% 0.09 165)   // deep teal-green
--accent-2:       oklch(48% 0.09 40)    // terracotta
--accent-ink:     #ffffff
--ok:             oklch(52% 0.10 160)
--warn:           oklch(60% 0.14 65)
--err:            oklch(52% 0.16 25)
--ring:           color-mix(in oklch, var(--accent) 28%, transparent)
```

### Tema B — "Forest & Cream"
```
--bg:             #12221c  (dark forest green)
--bg-2:           #0e1b17
--surface:        #1a2d26
--surface-2:      #203830
--surface-3:      #2a483d
--border:         #264037
--border-strong:  #35574a
--text:           #f1ead8  (warm cream)
--text-2:         #b5b09c
--muted:          #7d8078
--accent:         oklch(80% 0.13 90)    // warm yellow-gold
--accent-2:       oklch(70% 0.12 25)    // coral
--accent-ink:     #12221c
```

### Typografi
- **Sans:** `'IBM Plex Sans', system-ui, sans-serif` — 400 / 500 / 600 / 700
- **Mono:** `'IBM Plex Mono', ui-monospace, monospace` — 400 / 500
- Används för: belopp, IDs, filnamn, tidsstämplar, kodsnuttar
- **Display/serif:** `'Instrument Serif', Georgia, serif` — 400 regular + 400 italic
- Används för: hero-rubriker (<h1>), narrativ-datum i loggen

### Typografisk skala
- Hero h1: 40px, tight line-height (1.05), letter-spacing -0.02em
- Section h2: 14px, weight 500, uppercase-labels som småtext
- Body: 13–14px
- Mono-detaljer: 11.5–12px
- Captions/muted: 11px

### Spacing
- Base unit: 4px
- `--pad` (cozy): 12px / (compact): 8px
- `--pad-2` (cozy): 18px / (compact): 12px
- `--row-h` (cozy): 44px / (compact): 36px
- Standard gap mellan kort: 14–16px

### Border radius
- `--radius`: 8px (Tema A) / 10px (Tema B)
- `--radius-sm`: 5px / 6px

### Shadows
- Tema A: `0 1px 0 rgba(0,0,0,0.02), 0 1px 3px rgba(20,40,30,0.04)` — nästan osynlig, bara antydan
- Tema B: inset highlight + mörk drop-shadow
- Drawer: `-24px 0 60px -20px rgba(0,0,0,0.45)`

---

## Assets

**Fonter:** Laddas från Google Fonts i `<head>`:
```html
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500&family=Instrument+Serif:ital@0;1&display=swap" rel="stylesheet" />
```
I produktion: self-host via `next/font` eller motsvarande.

**Ikoner:** Egenskrivna linjeikoner i `src/icons.jsx`, 20×20 viewBox, 1.75px stroke, `currentColor`. Kan bytas ut mot Lucide/Heroicons — håll dig till linjestil (stroke, inte fill) för konsekvens.

**Leverantörs-logos:** Genereras från `name + hue` i `src/data.jsx` som färgade badges med 2-bokstavsinitialer. I produktion: hämta riktiga logos från Clearbit eller liknande, fall tillbaka till badges.

**PDF-preview:** Mockad i HTML/CSS i `src/review.jsx` (funktion `PdfMock`). I produktion: använd `react-pdf` eller visa embedded Google Drive preview via iframe.

---

## Språk / i18n

Prototypen stödjer svenska (default) och engelska. Svenska är primärt språk eftersom huvudmålgruppen är fi/sv-talande (Bezala är finskt, Visma-kunder).

I produktion:
- Lägg till finska som tredje språk (se SPEC.md Projekt 6.3)
- Migrera till `react-i18next` eller `next-intl`
- Datum/tal-formatering via `Intl.DateTimeFormat` och `Intl.NumberFormat`

All UI-text finns i `src/i18n.jsx` som utgångspunkt.

---

## Status-modell (viktig — läs denna)

Prototypen använder **två oberoende status-dimensioner** för varje meddelande, inte ett sammanslaget status-fält. Detta speglar verkligheten: fil-uppladdningen till Drive och överföringen till Bezala är separata pipeline-steg som kan lyckas eller misslyckas oberoende av varandra.

### `file_status` — vad hände med PDF-filen?

| Värde | Svensk label | Betydelse | Färg |
|---|---|---|---|
| `saved` | **Sparad** | Fil sparad till Google Drive | grön |
| `error` | **Fel** | Processing misslyckades (ingen bilaga, Drive-fel, etc.) | röd |
| `skipped` | **Hoppad** | AI avvisade som icke-kvitto, eller duplikat | grå |

### `bezala_status` — vad hände i Bezala?

| Värde | Svensk label | Betydelse | Färg |
|---|---|---|---|
| `transferred` | **Uppladdad** | Skickad till Bezala, kvittens erhållen | grön |
| `pending` | **Väntar** | Redo att skickas (väntar granskning eller tröskel) | gul |
| `error` | **Fel** | Upload till Bezala misslyckades | röd |
| `na` | *(ingen badge)* | Ej relevant — steget är inte planerat | — |

### Giltiga kombinationer

| file_status | bezala_status | UI-effekt |
|---|---|---|
| `saved` | `transferred` | 🟢 Sparad + 🟢 Uppladdad — happy path |
| `saved` | `pending` | 🟢 Sparad + 🟡 Väntar — kvitto i Drive, väntar granskning |
| `saved` | `error` | 🟢 Sparad + 🔴 Fel — i Drive, Bezala sa nej |
| `error` | `na` | 🔴 Fel (ingen Bezala-badge) |
| `skipped` | `na` | ⚪ Hoppad (ingen Bezala-badge) |

### Renderings-regler (icke förhandlingsbart)

1. **Två badges staplade** i tabellceller — fil-status ovanpå, Bezala-status under, 4px gap
2. När `bezala_status === 'na'` → **rendera inget** där Bezala-badgen skulle varit (inte ens en grå "—" placeholder). `<BezalaBadge>` returnerar `null` för `na`
3. Dot-indikator (`• label`) används på alla badges för tillgänglighet — färg ensam räcker inte
4. Labels översätts via `t.fileStatus` / `t.bezalaStatus` i `i18n.jsx`

### Var det används
- Översikt → tabellens status-kolumn (`StatusCell`)
- Logg → meddelande-tabellens status-kolumn (`StatusCell`)
- Pipeline-drawer → Bezala-vyn läser `bezala_status` för att välja banner-variant
- **Inte** i Granska-vyn — där är alla rader `bezala_status === 'pending'` per definition (kön är filtrerad)

---

## Filer

```
design_handoff_bezala_bot/
├── README.md                    ← denna fil
├── CLAUDE.md                    ← instruktioner till Claude Code (kopiera till repo-root)
├── SPEC.md                      ← kravspec (läs denna för implementation)
└── design/
    ├── index.html               ← hel prototyp, öppna för att se live
    └── src/
        ├── app.jsx              ← toppnivå, state, routing
        ├── data.jsx             ← mock-data (ska ersättas med API)
        ├── i18n.jsx             ← översättningar
        ├── theme.jsx            ← Tema A/B med alla CSS-variabler
        ├── icons.jsx            ← alla linjeikoner
        ├── components.jsx       ← GlobalStyles + Pill, VendorLogo, FileBadge, BezalaBadge, StatusCell, Confidence, Toast
        ├── dashboard.jsx        ← Översikt + Sidebar + TopBar
        ├── review.jsx           ← Granska-vy + PdfMock
        ├── log.jsx              ← Logg/Kommandocenter
        ├── settings.jsx         ← Inställningar
        ├── drawer.jsx           ← Pipeline-drawer (Gmail/AI/Drive/Bezala)
        └── tweaks.jsx           ← utvecklings-panel (ta bort i prod)
```

**Hur man kör prototypen lokalt:** Öppna `design/index.html` i en webbläsare. Ingen build, inga dependencies — allt laddas via CDN.

### Skärmdumpar (`screenshots/`)

Statiska referenser av varje vy för snabb orientering utan att behöva köra prototypen:

| Fil | Innehåll |
|---|---|
| `01-dashboard-top.png` | Översikt — hero, statistikkort, filter-tabs |
| `02-dashboard-table.png` | Översikt — tabell + scanning-stapel |
| `03-review.png` | Granska — kö, PDF-preview, formulär |
| `04-log-top.png` | Logg — KPI-strip + körningslista |
| `05-log-detail.png` | Logg — pipeline-tidslinje + token-statistik |
| `06-settings.png` | Inställningar |
| `07-drawer-gmail.png` | Pipeline-drawer: Gmail-mailvy |
| `08-drawer-ai.png` | Pipeline-drawer: AI-extraktion + resonemang |
| `09-drawer-drive.png` | Pipeline-drawer: Drive-fil + PDF-preview |
| `10-drawer-bezala.png` | Pipeline-drawer: Bezala-överföringsstatus |
| `11-theme-forest.png` | Alternativt tema "Forest & Cream" |

---

## Rekommenderad ordning för implementation

**Läs `SPEC.md`**. Där är allt indelat i 6 konkreta delprojekt med acceptanskriterier:

1. **Fundament** (auth, Gmail-koppling, DB-schema, API-skelett)
2. **AI-pipeline** (scanner, Claude-extraktor, Drive-uppladdare, Bezala-överförare)
3. **Frontend-integration** (byt mockar mot riktig data + drawer-endpoints)
4. **Granska-flödet** (edit-tracking, feedback-loop till AI, bulk-åtgärder)
5. **Observabilitet** (strukturerad loggning, schemaläggning, larm)
6. **Polering** (WCAG, responsiv, FI-språk, onboarding, empty states)

Varje projekt är självständigt och kan köras av separata agenter/utvecklare om så önskas.

---

## Kontakt / frågor

Öppna frågor som kräver produktbeslut (se SPEC.md Bilaga C):
- Multi-attachment per mail (ett eller flera messages?)
- Duplikat-detektion (blockera andra försök?)
- Mail utan bilaga (extrahera från body?)
- Flervaluta (växelkurs-hantering?)
- Retention (hur länge spara rå email-data?)
