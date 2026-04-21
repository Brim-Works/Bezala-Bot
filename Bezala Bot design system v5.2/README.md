# README.md — FAS 5 design-handoff

**Paket:** Bezala Bot FAS 5 — Soft-delete + Regelmotor + AI-inlärning + Kortmatchning
**Förutsättning:** FAS 4 merged till produktion. Denna migration bygger ovanpå det.

---

## Översikt

FAS 5 är en funktionsexpansion, inte en redesign. Design-språket från FAS 4 återanvänds i sin helhet — samma typografi, tokens, komponenter, färger.

Fyra nya features, en commit var:

| # | Feature | Ny vy | Backend-tabeller | Komplexitet |
|---|---------|-------|-----------------|-------------|
| 5.1 | Soft-delete & papperskorg | `trash` | Kolumner på ProcessedMessage | ⭐⭐ |
| 5.2 | Regelmotor | `rules` | `scan_rules` | ⭐⭐⭐ |
| 5.3 | AI-inlärning | `patterns` | `ai_feedback`, `vendor_patterns` | ⭐⭐⭐ |
| 5.4 | Kortmatchning | `cards` | `card_row_matches` | ⭐⭐⭐ (blockerare möjlig) |

## Designbeslut (motiverade)

### 5.1 Soft-delete

- **Auto-purge efter 60 dagar (default).** Konfigurerbart 30/60/90/aldrig i settings. 60 dagar är lång nog att fånga ångrade deletes men kort nog att inte svälla DB.
- **Drive-fil behålls vid hard-delete.** Default-beteende: bara DB-raden raderas, Drive är källarkiv. Opt-in via toggle i hard-delete-dialog att även radera Drive-kopian.
- **Gmail-etiketten tas bort vid soft-delete.** Detta tillåter mailet att re-scannas om det var en misstag-delete. Vid restore sätts etiketten tillbaka.

### 5.2 Regelmotor

- **Regex bakom avancerat-toggle.** Power-users får regex när de explicit aktiverar det per fält. Default är enkelt (exakt / innehåller / börjar med / slutar med). Regex-validering sker server-side; timeout 100ms.
- **Stale-regler auto-pausas INTE.** Visa indikator "0 matchningar senaste 30 dagar" — users bestämmer själva. Auto-paus är läskigt.
- **Ingen-match-fallback är synlig.** I Log-vyns detaljpanel markeras "Fångad av global fallback" som en neutral pill så users ser när regler missas.

### 5.3 AI-inlärning

- **Progressiv disclosure.** Feedback-knappar är subtila på Review-vyn. Full statistik + pattern-management ligger under Patterns-vyn, inte på dashboard.
- **Negativ feedback triggar inte silent bulk-ändring.** När ett nytt pattern skapas beräknas antal affekterade befintliga rader — users får CTA "47 liknande rader kan påverkas — granska dem?" men inget händer automatiskt.
- **VendorPattern vinner vid konflikt.** Pattern är explicit användar-lärd data. Om AI hittar avvikande värde med hög confidence flaggas fältet som "Ovanligt för denna leverantör" — users behåller kontroll.

### 5.4 Kortmatchning

- **Default tolerance ±3 dagar.** Kortdebitering sker oftast 1–3 dagar efter köp, men helger sträcker ut det. Konfigurerbart 1–7.
- **Auto-match-tröskel 97%.** Strängare än AI-extraktion (90%) eftersom konsekvensen är att fel kvitto bifogas i bokföring. Konfigurerbart med varning under 95%.
- **Multi-match tvingar manuell bekräftelse.** Aldrig auto-match när en kortrad matchar flera kvitton eller vice versa. Visa alla kandidater sida vid sida.

---

## Design-tokens (oförändrade från FAS 4)

Definieras i `design/src/theme.jsx` (motsvarar `tokens.css` i produktionen).

```
--bg           (ljust: #FBF9F5     / skog: #1a2332)
--bg-elev      (ljust: #F3EFE7     / skog: #22303F)
--surface      (ljust: #FFFFFF     / skog: #2C3A4B)
--border       (ljust: #E5DFD2     / skog: #3B4B5E)
--border-strong(ljust: #C9BFA8     / skog: #556A81)

--text         (ljust: #1A1916     / skog: #E8ECF0)
--text-dim     (ljust: #58544C     / skog: #AEB9C5)
--text-muted   (ljust: #8A857A     / skog: #7E8B9A)

--accent       (ljust: #8B6A3E     / skog: #D4A373)
--accent-contrast (ljust: #FFFFFF  / skog: #1A2028)

--ok     #3E8E5E
--warn   #C08B2E
--err    #B84545
--info   #4B7FAE
```

## Typografi (oförändrad)

- **Instrument Serif** (italic, weight 300) — h2/h3 sektionsrubriker, stora tal, empty-state-titles
- **IBM Plex Sans** (400/500/600) — all brödtext, knappar, labels, nav
- **IBM Plex Mono** (400/500) — belopp, datum, IDs, filnamn, hex-värden

## Komponent-inventering

### Återanvända från FAS 4 (utan ändring)

- `<Pill kind="ok|warn|err|info|neutral|accent">` — status-märken
- `<StatusCell>` — inline-status med ikon
- `<VendorLogo vendor={name, logo, hue}>` — färgad initialbox
- `<Confidence value={0..1}>` — procent + stapel
- `<Toast message>` — bottom-left ephemeral
- `<TopBar>` — översta raden
- `<TweaksPanel>` — tweaks-integration
- `<Drawer>` — pipeline-detalj-panel
- `GlobalStyles`, `applyTheme()` — tema-applicering

### Nya i FAS 5

Definierade i `design/src/fas5-views.jsx`:

- `<TrashScreen>` — hela trash-vyn
- `<RulesScreen>` + `<RuleCard>` + `<RuleEditor>` — regel-management
- `<PatternsScreen>` — AI-learning-vy
- `<CardMatchScreen>` + `<CardGroup>` + `<CardDetail>` — kortmatchning
- `<FeedbackInline field value aiValue>` — tummar upp/ner per fält (Review)
- `<LearnPromptBanner>` — inline "lär-prompt" (Review)
- `<Checkbox>` — för bulk-select
- `<Btn variant="primary|ghost|danger" size="sm|md">` — knapp
- `<SectionHead title sub actions>` — sidhuvud-mönster
- `<EmptyState icon title sub>` — tom-tillstånd
- `<StatTile label value mono accent>` — siffer-kort
- `<Field label>`, `<Divider>`, `<KV>` — formulär-primitiver

Definierade i `design/src/fas5-sidebar.jsx`:

- `<Fas5Sidebar>` — sidebar med 4 nya nav-items + separator

## Filstruktur i paketet

```
fas5/
├── CLAUDE.md                   # Anchor-fil för Claude Code
├── SPEC.md                     # Migrationsplan (4 commits)
├── README.md                   # Detta dokument
├── index.html                  # Prototyp-startpunkt
├── design/
│   └── src/
│       ├── data.jsx            # FAS 4 mock-data (oförändrad)
│       ├── i18n.jsx            # FAS 4 översättningar (oförändrad)
│       ├── icons.jsx           # FAS 4 ikon-set (oförändrad)
│       ├── theme.jsx           # FAS 4 tokens + tema (oförändrad)
│       ├── components.jsx      # FAS 4 Pill/StatusCell/Toast/etc (oförändrad)
│       ├── dashboard.jsx       # FAS 4 Dashboard + Sidebar + TopBar (oförändrad)
│       ├── review.jsx          # FAS 4 ReviewScreen (oförändrad)
│       ├── settings.jsx        # FAS 4 SettingsScreen (oförändrad)
│       ├── log.jsx             # FAS 4 LogScreen (oförändrad)
│       ├── drawer.jsx          # FAS 4 Drawer (oförändrad)
│       ├── tweaks.jsx          # FAS 4 TweaksPanel (oförändrad)
│       ├── fas5-i18n.jsx       # NYT: sv/en-översättningar för 4 nya vyer
│       ├── fas5-data.jsx       # NYT: mock-data för trash/rules/patterns/cards
│       ├── fas5-views.jsx      # NYT: alla 4 nya vyer + FeedbackInline + LearnPromptBanner
│       ├── fas5-sidebar.jsx    # NYT: utökad Sidebar (4 nya nav-items)
│       └── fas5-app.jsx        # NYT: app-shell som mountar allt
└── screenshots/                # Pixel-referenser
```

## Att köra prototypen

Öppna `fas5/index.html` i en browser. Inga bygg-steg, inga dependencies utöver CDN-React.

- Byt vy i sidebar — state persist:as i localStorage
- Tweaks-läge (toolbar-toggle) exponerar variant/language/density
- Undo-toast testas via trash → delete (mock)
- Rule-editor öppnas via "Redigera" på ett regelkort
- Card-match-detalj visas när du klickar en rad i vänsterkolumnen

## För Claude Code — börja här

1. Läs `CLAUDE.md` i sin helhet
2. Läs `SPEC.md` commit 5.1
3. Studera relevanta `design/src/fas5-*.jsx` och screenshots
4. Inventera produktions-kodbasens motsvarande filer
5. Bekräfta-läsning (3 förbjudna saker, filer som ändras, TODO:s)
6. Presentera plan för commit 5.1 → invänta godkännande
7. Koda commit 5.1 → PR → merge → gå till 5.2

**Inga commits i en enda PR.** En commit = en PR.

## Kända blockerare att verifiera

- **5.4** Bezala Public API måste ha endpoint för att lista kortrader utan bifogat kvitto. Om inte — fråga user innan du bygger wrappern. Frontend + match-service kan ändå byggas mot mock/stub.

## Nya beroenden (fråga före install)

- **5.2:** DnD-library (alternativ: upp/ner-pilar utan dep)
- **5.2:** `regex` lib för timeout (alternativ: stdlib `re` utan timeout — OK för per-user regler)
- **5.4:** `rapidfuzz` för snabbare fuzzy-match (alternativ: `difflib` stdlib)

## Språk

Svenska är default. Alla strängar finns i både `sv` och `en` i `fas5-i18n.jsx` och ska speglas i produktionens `frontend/src/i18n.js`.

## Ikoner (för referens)

Alla FAS 5-vyer använder endast befintliga ikoner från `icons.jsx` (FAS 4):
- `I.X` — trash, dismiss
- `I.Filter` — rules
- `I.Sparkle` — AI/learning
- `I.Bezala` — kortmatchning-nav (knyter till Bezala-pipeline)
- `I.Check`, `I.Plus`, `I.Search`, `I.ArrowL`, `I.Sliders`, `I.Mail`, `I.File`, `I.Clock` — kontextuellt

Inga nya ikoner krävs.
