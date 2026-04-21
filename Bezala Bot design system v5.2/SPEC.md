# SPEC.md — FAS 5 migration plan

Fyra commits. Varje commit är en PR, mergas separat, körs till prod mellan varje.

---

## Commit 5.1 — Soft-delete + papperskorg

**Mål:** Användaren kan ta bort irrelevanta rader utan permanent dataförlust.
Borttagna rader hamnar i papperskorg i 60 dagar innan auto-purge.

### Backend

**Migration:**
```python
# alembic/versions/NNN_soft_delete_messages.py
op.add_column('processed_messages',
  sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True))
op.add_column('processed_messages',
  sa.Column('delete_reason', sa.String(32), nullable=True))  # calendar|spam|misclassified|manual
op.create_index('ix_processed_messages_deleted_at', 'processed_messages', ['deleted_at'])
```

**Model:**
```python
class ProcessedMessage(Base):
    # ... befintliga fält
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delete_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
```

**Endpoints:**

| Method | Path | Beteende |
|---|---|---|
| `GET`    | `/api/messages`                       | Exkludera `deleted_at IS NOT NULL` som default |
| `GET`    | `/api/messages/trash`                 | Returnera `deleted_at IS NOT NULL`, sorterat desc |
| `DELETE` | `/api/messages/{id}`                  | Soft-delete: sätt `deleted_at=now()`, `delete_reason` från body |
| `POST`   | `/api/messages/{id}/restore`          | Sätt `deleted_at=NULL`, `delete_reason=NULL` |
| `DELETE` | `/api/messages/{id}?permanent=true`   | Hard-delete rad (Drive-fil behålls) |
| `DELETE` | `/api/messages/trash`                 | Hard-delete alla rader där `deleted_at IS NOT NULL` |

**Pipeline-sidoeffekt vid soft-delete:**
- Ta bort `Bezala-Klar`-etiketten i Gmail (via befintlig Gmail-klient)
- Vid restore: sätt tillbaka etiketten

**Auto-purge:**
- Ny scheduler-job `purge_old_trash()` i befintlig APScheduler-instans
- Körs 1 gång/dag (kl 03:00 UTC)
- Raderar rader där `deleted_at < now() - interval '60 days'`
- Konfigurerbart via settings (`TRASH_RETENTION_DAYS`, default 60)

**Drive-fil vid hard-delete:**
- **Default:** Behåll i Drive, markera `drive_link_broken=True` i DB (ny kolumn, optional)
- Query-param `?purge_drive=true` → radera även Drive-fil
- UI exponerar toggle i hard-delete-dialog

### Frontend

**Nav:**
- Ny nav-item `trash` i sidebar med räknare
- Räknare = antal rader i papperskorg (polling var 60s räcker — inget realtid-krav)

**Dashboard-tabell + Review-queue + Log:**
- Lägg till checkbox-kolumn längst till vänster
- Bulk-bar visas ovanför tabellen när 1+ markerad: "N markerade" + "Ta bort"
- Per-rad: trash-ikon i action-kolumnen

**Trash-vy:** se `design/src/fas5-views.jsx` → `<TrashScreen>`
- Tabell med checkbox / borttagen-datum / leverantör / ämne / belopp / anledning-pill / actions
- Bulk-actions: "Återställ markerade", "Radera permanent markerade"
- Sidhuvud-action: "Töm papperskorgen" (confirm-dialog)

**Undo-toast:**
- Direkt efter delete: `"Rad borttagen — Ångra"` (5 sek)
- Klick på "Ångra" → POST /api/messages/:id/restore
- Återanvänd befintlig `<Toast>`-komponent, lägg till `action`-prop

### Acceptanskriterier

- [ ] Soft-deletad rad försvinner från Dashboard/Review/Log inom 1 sekund
- [ ] Samma rad syns i papperskorg med korrekt delete_reason
- [ ] Undo-toast återställer raden inom 5 sekunder (klickbart)
- [ ] Hard-delete tar bort raden permanent från DB (behåller Drive-fil default)
- [ ] Töm papperskorgen raderar alla soft-deletade rader
- [ ] Scheduler purgar rader äldre än 60 dagar
- [ ] Båda teman (Ljust + Skog) renderar trash-vyn korrekt
- [ ] Svenska + engelska översättningar stämmer
- [ ] Bulk-select fungerar med shift-click för range-select (nice-to-have, ej blocker)

### Filer som skapas
- `alembic/versions/NNN_soft_delete_messages.py`
- `app/api/trash.py` (nya endpoints)
- `app/services/trash_scheduler.py` (purge-job)
- `frontend/src/views/Trash.jsx`
- `frontend/src/components/TrashBulkBar.jsx`
- `frontend/src/components/UndoToast.jsx`

### Filer som ändras
- `app/models.py` (kolumner)
- `app/api/messages.py` (filtrera deleted_at, DELETE endpoint)
- `app/main.py` (registrera trash-router)
- `frontend/src/App.jsx` (ny route)
- `frontend/src/components/Sidebar.jsx` (nav-item)
- `frontend/src/views/Dashboard.jsx`, `Review.jsx`, `Log.jsx` (checkboxar)
- `frontend/src/i18n.js` (nya nycklar)

---

## Commit 5.2 — Regelmotor (scan rules)

**Mål:** Ersätt enkelt filter med namngivna regler. Första matchande regel vinner.
Användaren kan testa, duplicera, pausa, prioritera.

### Backend

**Migration:**
```python
# alembic/versions/NNN_scan_rules.py
op.create_table('scan_rules',
  sa.Column('id', sa.Integer, primary_key=True),
  sa.Column('user_id', sa.Integer, sa.ForeignKey('users.id'), nullable=False),
  sa.Column('name', sa.String(100), nullable=False),
  sa.Column('priority', sa.Integer, nullable=False, default=0),  # lower = higher prio
  sa.Column('active', sa.Boolean, nullable=False, default=True),
  sa.Column('match_config', postgresql.JSONB, nullable=False),   # {from, subject_any[], has_attachment, min_amount, currency, regex, language, date_range}
  sa.Column('action_config', postgresql.JSONB, nullable=False),  # {category, bezala_account, auto_upload, notify_first, drive_folder, bezala_tags}
  sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
  sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
  sa.Column('last_matched_at', sa.DateTime(timezone=True), nullable=True),
  sa.Column('match_count_30d', sa.Integer, nullable=False, default=0)
)
op.create_index('ix_scan_rules_user_priority', 'scan_rules', ['user_id', 'priority'])
```

**Endpoints:**

| Method | Path | Beteende |
|---|---|---|
| `GET`    | `/api/rules`                     | Lista alla regler sorterade på priority |
| `POST`   | `/api/rules`                     | Skapa ny regel (auto-priority = max+1) |
| `PUT`    | `/api/rules/{id}`                | Uppdatera namn/match/action/active |
| `DELETE` | `/api/rules/{id}`                | Radera regel |
| `POST`   | `/api/rules/reorder`             | Body: `{order: [id1, id2, ...]}` → uppdatera priority |
| `POST`   | `/api/rules/{id}/test`           | Dry-run mot befintliga mail (senaste 90 dagar), returnera matchande IDs + count |
| `POST`   | `/api/rules/{id}/duplicate`      | Skapa kopia med `" (kopia)"` i namn |

**Pipeline-integration:**
- Ersätt nuvarande filter-läsning med `apply_rules(message) -> (matched_rule: ScanRule | None, action: dict)`
- Iterera regler i priority-ordning, första match vinner
- Om ingen match → använd global fallback (befintlig default-config)
- Uppdatera `last_matched_at` + `match_count_30d` (räknare återställs nattligt via scheduler)
- Logga vilken regel som matchade i `ProcessedMessage.matched_rule_id` (ny nullable FK-kolumn)

**Regex-stöd:**
- Per matchningsfält, opt-in: `match_config.from_regex=true` → behandla `match_config.from` som regex
- Validera regex server-side med `re.compile` i POST/PUT — returnera 400 vid syntax-fel
- **Säkerhet:** Tidsbegräns regex-matchning med `regex` lib + `TIMEOUT=100ms`, logga timeout-fall

**Regel-templates:**
- Hårdkodad lista i `app/services/rule_templates.py` med 5 förifyllda exempel (Flygresa, Taxi, Hotell, Restaurang, Kontorsmaterial)
- Frontend visar dem vid "Ny regel" som snabbval

### Frontend

**Flyttad:** Inställningssidans nuvarande filter-sektion flyttas → länka till `rules`-vyn istället.

**Rules-vy:** se `design/src/fas5-views.jsx` → `<RulesScreen>` + `<RuleCard>` + `<RuleEditor>`
- Lista av regelkort i priority-ordning
- Drag-handles (upp/ner-pilar minimum; DnD-library om time permits)
- Side-panel-editor (öppnas vid "Redigera")
- Avancerat-toggle exponerar: regex-checkbox, språk, datum-intervall
- "Test regel"-knapp → visa modal med "N mail skulle matchat" + lista på de 10 första
- Global fallback-kort längst ner (read-only mini-edit)
- Stale-indicator: om `match_count_30d === 0` visa warn-pill

### Acceptanskriterier

- [ ] Skapa, redigera, duplicera, pausa, radera regel fungerar
- [ ] Prioritet-omsortering persist:ar till DB
- [ ] Pipeline respekterar regelpriority — första match vinner
- [ ] Dry-run returnerar samma resultat som riktig matchning hade gjort
- [ ] Regex-fel ger 400 med läsbart felmeddelande
- [ ] Globala fallback-inställningar fortfarande tillämpas när ingen regel matchar
- [ ] Log-vyn visar vilken regel (eller "global fallback") som fångat varje mail
- [ ] Pausade regler hoppas över i pipeline men bevaras i DB
- [ ] Stale-indicator visas för regler med 0 matchningar senaste 30 dagar

### Filer som skapas
- `alembic/versions/NNN_scan_rules.py`
- `app/models/scan_rule.py` (ScanRule)
- `app/api/rules.py`
- `app/services/rule_engine.py` (apply_rules + matching-logik)
- `app/services/rule_templates.py`
- `frontend/src/views/Rules.jsx`
- `frontend/src/components/RuleCard.jsx`
- `frontend/src/components/RuleEditor.jsx`
- `frontend/src/components/RuleTestModal.jsx`

### Filer som ändras
- `app/services/pipeline.py` (ersätt filter-läsning med rule_engine)
- `app/models.py` (ny FK `matched_rule_id` på ProcessedMessage)
- `app/main.py` (registrera rules-router)
- `frontend/src/App.jsx` (ny route)
- `frontend/src/components/Sidebar.jsx`
- `frontend/src/views/Settings.jsx` (ta bort filter-sektion, lägg länk till rules)
- `frontend/src/i18n.js`

### Beroende-förfrågan
- DnD: **fråga user** innan installation. Alternativ: bara upp/ner-pilar (ingen ny dep).
- Regex-säkerhet: om `regex` lib ej finns, fråga innan install (alternativt stdlib `re` utan timeout — acceptabel trade-off för per-user regler).

---

## Commit 5.3 — AI-inlärning (Nivå 1 + 2)

**Mål:** Systemet samlar feedback per AI-gissat fält och lär sig leverantörs-specifika mönster.

### Backend

**Migrations:**
```python
# alembic/versions/NNN_ai_feedback.py
op.create_table('ai_feedback',
  sa.Column('id', sa.Integer, primary_key=True),
  sa.Column('message_id', sa.Integer, sa.ForeignKey('processed_messages.id', ondelete='CASCADE')),
  sa.Column('user_id', sa.Integer, sa.ForeignKey('users.id')),
  sa.Column('field_name', sa.String(40), nullable=False),
  sa.Column('ai_value', sa.Text, nullable=True),
  sa.Column('user_value', sa.Text, nullable=True),
  sa.Column('feedback_type', sa.String(10), nullable=False),  # positive|negative
  sa.Column('user_comment', sa.Text, nullable=True),
  sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now())
)
op.create_index('ix_ai_feedback_message', 'ai_feedback', ['message_id'])
op.create_index('ix_ai_feedback_user_created', 'ai_feedback', ['user_id', 'created_at'])

op.create_table('vendor_patterns',
  sa.Column('id', sa.Integer, primary_key=True),
  sa.Column('user_id', sa.Integer, sa.ForeignKey('users.id')),
  sa.Column('vendor_name', sa.String(200), nullable=False),
  sa.Column('fields', postgresql.JSONB, nullable=False),  # {category: "Resa", bezala_account: "5811", currency: "EUR", ...}
  sa.Column('learned_from_count', sa.Integer, nullable=False, default=1),
  sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
  sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
  sa.UniqueConstraint('user_id', 'vendor_name', name='uq_user_vendor')
)
```

**Endpoints:**

| Method | Path | Beteende |
|---|---|---|
| `POST`   | `/api/messages/{id}/feedback`       | Body: `{field, feedback_type, ai_value, user_value?, comment?}` |
| `GET`    | `/api/patterns`                      | Lista alla patterns för user |
| `DELETE` | `/api/patterns/{id}`                 | "Glöm" pattern |
| `GET`    | `/api/stats/feedback`                | Stats: `{positive_7d, negative_7d, hit_rate}` |

**Learn-prompt-trigger (Review-vyn):**
- När user ändrar ett fält där `ai_value != user_value`:
  - Frontend anropar `POST /api/messages/{id}/feedback` med `feedback_type='negative'`
  - Frontend visar `<LearnPromptBanner>` → "Ska {field}={user_value} användas som standard för {vendor}?"
  - Vid Ja → `POST /api/patterns` (eller uppdatera befintlig)

**AI-prompt-injection:**
- I `app/services/ai_extractor.py`, innan Claude-anropet:
  - Hämta `VendorPattern` matchande (user_id, extrahera vendor-gissning från ämne/avsändare — fuzzy match ≥85% similarity)
  - Om pattern finns: injicera i prompten som `<vendor_hints>{...}</vendor_hints>` före mail-content
  - Claude instrueras: "These are learned defaults — prefer them unless clear evidence otherwise"
- Om AI returnerar avvikande värde med hög confidence (≥90%) från pattern → markera fältet med `ai_differs_from_pattern=true` → frontend visar "Ovanligt för denna leverantör" varning

**Retro-korrigering:**
- Vid negativ feedback som skapar pattern: beräkna antal befintliga ProcessedMessage med samma vendor där AI-gissningen inte matchar nya patternet
- Returnera i feedback-endpoint: `{affected_count: N, message_ids: [...]}`
- Frontend visar CTA: "47 liknande rader kan påverkas — granska dem?" (länk till filtrerad Log-vy)
- **INGEN auto-omkörning.** User väljer manuellt att granska.

### Frontend

**Feedback-knappar:**
- Se `design/src/fas5-views.jsx` → `<FeedbackInline field value aiValue>`
- Bredvid varje AI-extraherat fält i Review-vyn
- Tummar upp / ner, muted färg tills hover
- Tummar ner öppnar inline-kommentar-input (Enter/blur submit)

**Learn-prompt-banner:**
- Se `<LearnPromptBanner>` — diskret bg-elev banner inline under fält
- Visas bara en gång per ändring, dismissas vid No

**Patterns-vy:**
- Se `<PatternsScreen>` — 3 stat-tiles + patterns-kort-grid
- Varje kort: vendor-logo, vendor-name, key-value-lista, "Glöm detta mönster"-X-knapp
- Empty-state vid 0 patterns

**Transparens-regel:**
- Dashboard visar INTE feedback-statistik (håll dashboard fokuserad på kvitton)
- Patterns-vyn ligger under Settings-relaterade nav-items (grupperad sekundär)
- Visa `ai_differs_from_pattern`-varning inline i Review-vyn med liten pill

### Acceptanskriterier

- [ ] Tummar upp/ner per fält skriver AiFeedback-rad
- [ ] Negativ feedback med user_value → visar Learn-prompt-banner
- [ ] "Ja, lär" skapar/uppdaterar VendorPattern
- [ ] Nästa mail från samma vendor: AI får patterns som hints i prompten
- [ ] Stats-endpoint returnerar korrekt hit-rate för 7 dagar
- [ ] "Glöm detta mönster" raderar VendorPattern
- [ ] Retro-korrigering-CTA visas endast när affected_count > 0
- [ ] ai_differs_from_pattern-varning visas korrekt i Review
- [ ] Ingen bulk-auto-omkörning sker (user måste godkänna)

### Filer som skapas
- `alembic/versions/NNN_ai_feedback.py`
- `app/models/ai_feedback.py`
- `app/models/vendor_pattern.py`
- `app/api/feedback.py`
- `app/api/patterns.py`
- `app/services/pattern_matcher.py` (fuzzy vendor-matchning)
- `frontend/src/views/Patterns.jsx`
- `frontend/src/components/FeedbackInline.jsx`
- `frontend/src/components/LearnPromptBanner.jsx`

### Filer som ändras
- `app/services/ai_extractor.py` (prompt-injection + diff-detection)
- `app/api/stats.py` (feedback-stats)
- `app/main.py`
- `frontend/src/views/Review.jsx` (FeedbackInline per fält + LearnPromptBanner)
- `frontend/src/components/Sidebar.jsx`
- `frontend/src/i18n.js`

---

## Commit 5.4 — Kortmatchning

**Mål:** Hitta kortrader i Bezala som saknar kvitto, matcha automatiskt mot Bezala Bot-DB, bifoga vid hög confidence.

### ⚠️ Pre-flight BLOCKER — verifiera först

**TODO för Claude Code innan start:**
Verifiera att Bezala Public API exponerar en endpoint för att lista kortrader UTAN bifogat kvitto. Om inte:
- Fråga user om det finns ett internt sätt (webhook, CSV-export, direkt DB-access?)
- Dokumentera blockerare i commit-PR
- Bygg frontend + match-service ändå, men lämna wrapper stub med mock-data

### Backend

**Migration:**
```python
op.create_table('card_row_matches',
  sa.Column('id', sa.Integer, primary_key=True),
  sa.Column('user_id', sa.Integer, sa.ForeignKey('users.id')),
  sa.Column('bezala_card_row_id', sa.String(100), nullable=False),
  sa.Column('card_date', sa.Date, nullable=False),
  sa.Column('card_amount', sa.Numeric(10, 2), nullable=False),
  sa.Column('card_currency', sa.String(3), nullable=False),
  sa.Column('card_vendor_raw', sa.Text, nullable=True),
  sa.Column('matched_message_id', sa.Integer, sa.ForeignKey('processed_messages.id'), nullable=True),
  sa.Column('status', sa.String(16), nullable=False),  # suggested|auto|confirmed|orphan|manual
  sa.Column('confidence', sa.Float, nullable=True),
  sa.Column('match_reasons', postgresql.JSONB, nullable=True),
  sa.Column('attached_at', sa.DateTime(timezone=True), nullable=True),
  sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now())
)
op.create_index('ix_card_matches_status', 'card_row_matches', ['user_id', 'status'])
```

**Bezala-wrapper (stub om endpoint saknas):**
```python
# app/services/bezala_card_sync.py
def list_card_rows_without_receipt(user_id: int) -> list[CardRow]:
    """Returnera kortrader från Bezala som inte har bifogat kvitto."""
    # TODO: Verifiera Bezala API-endpoint
    ...

def attach_receipt_to_card_row(card_row_id: str, drive_file_id: str) -> None:
    """Bifoga Drive-fil till Bezala-kortrad."""
    ...
```

**Match-service:**
```python
# app/services/card_matcher.py
def find_matching_receipts(card: CardRow, tolerance_days=3, amount_tolerance=1.0) -> list[Match]:
    """
    Returnera kandidat-kvitton från ProcessedMessage, sorterat på confidence desc.
    - Datum inom ±tolerance_days (default 3, konfigurerbart 1-7)
    - Belopp inom ±amount_tolerance (default 1.0 för avrundning)
    - Samma valuta
    - Fuzzy vendor-match (≥70% similarity)
    - Confidence = viktad kombination:
        amount_match (40%) + date_diff (20%) + vendor_similarity (30%) + currency_match (10%)
    """

def run_matching(user_id: int) -> MatchResult:
    """Kör matchning för en user, spara CardRowMatch-rader."""
```

**Auto-attach regel:**
- Confidence ≥ 97% OCH endast en kandidat över 70% → `status='auto'`, bifoga direkt via Bezala-API, sätt `attached_at=now()`
- **Multi-match (ett kvitto matchar flera kortrader) → ALDRIG auto-match.** Sätt alla berörda till `suggested`.
- Confidence 70–97% → `status='suggested'`, visa i UI för manuell bekräftelse
- Confidence < 70% eller 0 kandidater → `status='orphan'`

**Scheduler:**
- Ny job `match_card_rows()` i APScheduler
- Kör 1 gång/dag (06:00 UTC)
- Konfigurerbart manuell trigger via `POST /api/card-rows/run-matching`

**Endpoints:**

| Method | Path | Beteende |
|---|---|---|
| `GET`    | `/api/card-rows/unmatched`          | Lista: `{suggested: [], auto_today: [], orphan: []}` |
| `POST`   | `/api/card-rows/{id}/confirm`       | Body: `{candidate_message_id}` → bifoga + sätt `status='confirmed'` |
| `POST`   | `/api/card-rows/{id}/ignore`        | Body: `{candidate_message_id?}` → ta bort kandidat eller markera orphan som manual |
| `POST`   | `/api/card-rows/run-matching`       | Manuell trigger |

### Frontend

**Nav:**
- Ny nav-item `cards` med räknare (antal suggested)

**Kortmatchning-vy:** se `<CardMatchScreen>` + `<CardGroup>` + `<CardDetail>`
- 2-kolumn-layout: lista vänster (3 grupper: suggested / auto-matchade idag / orphaned) + detalj höger
- Detalj-panel: kortrad-header + kandidat-kort per förslag
- Per kandidat: vendor-logo, belopp, datum, filnamn, confidence-bar, match-reasons, "Bekräfta"/"Inte denna"
- Orphan: "Markera för manuell hantering"-knapp
- Auto-matchade visar "Auto-bifogat"-pill (read-only, historik)

**Settings:**
- Ny sektion "Kortmatchning":
  - Tolerance-slider: ±1 till ±7 dagar (default 3)
  - Auto-match-tröskel-slider: 90%–99% (default 97%, varning under 95%)
  - Toggle: "Kör automatisk matchning dagligen" (default on)

### Acceptanskriterier

- [ ] Dagligt scheduler-job kör utan fel (även om Bezala-endpoint är stubbad)
- [ ] Kortrader grupperas korrekt i suggested/auto/orphan
- [ ] Confidence-beräkning returnerar värden 0–1 med korrekt viktning
- [ ] Auto-match triggas endast vid ≥97% confidence OCH ingen multi-match
- [ ] Multi-match (en kortrad → flera >70%-kvitton) hamnar i suggested (aldrig auto)
- [ ] "Bekräfta" anropar Bezala attach-endpoint och sätter status='confirmed'
- [ ] "Inte denna" tar bort kandidat, behåller övriga
- [ ] Orphan "Markera för manuell" sätter status='manual'
- [ ] Settings-slider för tolerance/tröskel persistar och används i nästa körning
- [ ] Varning visas om auto-match-tröskel < 95%

### Filer som skapas
- `alembic/versions/NNN_card_matches.py`
- `app/models/card_row_match.py`
- `app/api/card_rows.py`
- `app/services/bezala_card_sync.py` **(med TODO-blockerare)**
- `app/services/card_matcher.py`
- `app/services/card_scheduler.py`
- `frontend/src/views/CardMatching.jsx`
- `frontend/src/components/CardGroup.jsx`
- `frontend/src/components/CardDetail.jsx`
- `frontend/src/components/CardMatchSettings.jsx`

### Filer som ändras
- `app/main.py` (registrera card-rows-router)
- `app/scheduler.py` (lägg till card-matching-job)
- `app/api/settings.py` (tolerance/tröskel-fält)
- `frontend/src/views/Settings.jsx` (ny sektion)
- `frontend/src/components/Sidebar.jsx`
- `frontend/src/i18n.js`

### Beroende-förfrågan
- Fuzzy-matching: Python stdlib `difflib.SequenceMatcher` räcker för vendor-similarity. Om bättre behövs → **fråga** (`rapidfuzz` är lätt men nytt dep).
- Frontend behöver inget nytt.

---

## Globala acceptanskriterier (gäller alla 4 commits)

- [ ] Båda teman (Ljust + Skog) renderar utan CSS-fel
- [ ] Svenska är default, engelska fungerar genom hela flödet
- [ ] Inga nya console-errors i devtools
- [ ] Befintliga FAS 4-tester fortsätter passera
- [ ] Varje commit har ≥ 3 nya backend-tester (happy path + edge case + auth)
- [ ] Inga nya emojis, inga nya fontfamiljer, 1.75px ikoner överallt
- [ ] Session-auth respekteras på alla nya endpoints (befintlig `Depends(current_user)`)
