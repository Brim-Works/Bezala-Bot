# Bezala Bot

Scannar Gmail automatiskt varje timme, hittar kvitton och resedokument,
namnger dem med Claude AI och sparar till Google Drive. Allt loggas i
PostgreSQL och visualiseras i en React-dashboard. Deployas på Railway.

## Stack

- **Backend:** FastAPI + APScheduler (Python 3.11)
- **Frontend:** React + Vite (servas statiskt av FastAPI)
- **Databas:** PostgreSQL (Railway Plugin)
- **Integrationer:** Gmail API, Google Drive, Claude AI, Bezala API

## Funktioner

1. **Automatisk Gmail-scanning** varje timme (APScheduler).
2. **Filterar bort** reklam/social/updates/spam/trash + mail utan bilagor.
3. **PDF-validering** via magic bytes (`b'%PDF'`).
4. **Claude AI** namnger varje fil: `20260401 Finnair HEL-CPH.pdf`.
5. **Google Drive-uppladdning** via service account.
6. **PostgreSQL-logg** av varje bearbetat mail (avsändare, ämne, filnamn, status, fel).
7. **Dubblettskydd i 3 lager:**
   - Gmail-etiketten `Bezala-Klar` sätts efter uppladdning
   - `message_id` lagras unikt i DB
   - `(filnamn, datum)` unikt index + kontroll i Drive
8. **React-dashboard** med statistik, logg och PDF-preview.

## Projektstruktur

```
bezala-bot/
├── app/
│   ├── main.py              # FastAPI entrypoint + statisk frontend
│   ├── config.py            # pydantic-settings
│   ├── db.py                # SQLAlchemy
│   ├── models.py            # ProcessedMessage, SavedFile, ScanRun
│   ├── scheduler.py         # APScheduler
│   └── services/
│       ├── gmail_client.py
│       ├── drive_client.py
│       ├── ai_namer.py      # Claude-namngivning
│       ├── pdf_validator.py
│       ├── bezala_client.py
│       └── pipeline.py      # Huvudlogik + dubblettskydd
├── frontend/                # React + Vite
├── scripts/
│   └── generate_token.py    # Lokal Gmail OAuth-helper
├── requirements.txt
├── Procfile
├── railway.toml
├── nixpacks.toml
└── .env.example
```

## Lokal utveckling

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # fyll i värden

# Backend
uvicorn app.main:app --reload

# Frontend (separat terminal)
cd frontend && npm install && npm run dev
```

Hälsokoll: `curl http://localhost:8000/health`
Dashboard: `http://localhost:5173`

## Engångssetup — Gmail OAuth

Gmail kräver en `refresh_token` som genereras en gång lokalt och sedan läggs in
som miljövariabel i Railway.

1. Skapa projekt i [Google Cloud Console](https://console.cloud.google.com/).
2. Aktivera **Gmail API**.
3. Skapa OAuth-credentials av typen **Desktop app**.
4. Ladda ner JSON-filen och spara som `gmail_credentials.json` i projektroten.
5. Kör:

   ```bash
   python scripts/generate_token.py
   ```

6. Logga in i webbläsaren som öppnas. Skriptet skriver ut tre värden:

   ```
   GMAIL_CLIENT_ID=...
   GMAIL_CLIENT_SECRET=...
   GMAIL_REFRESH_TOKEN=...
   ```

7. Klistra in i Railway → **Variables**.

> `gmail_credentials.json` finns i `.gitignore` och checkas aldrig in.

## Engångssetup — Google Drive (service account)

1. Google Cloud Console → **IAM & Admin** → **Service Accounts** → skapa.
2. **Keys** → **Add Key** → **JSON** → ladda ner.
3. Öppna JSON-filen och kopiera hela innehållet (inkl. klammerparenteser).
4. Klistra in som `GOOGLE_SERVICE_ACCOUNT_JSON` i Railway.
5. Öppna Drive-mappen i webbläsaren, dela med service account-e-posten
   (Editor-behörighet). Kopiera mapp-ID från URL:en → `GOOGLE_DRIVE_FOLDER_ID`.

## Railway — miljövariabler

| Variabel | Beskrivning |
| --- | --- |
| `ANTHROPIC_API_KEY` | Claude API-nyckel |
| `BEZALA_USERNAME` | Bezala-användarnamn |
| `BEZALA_PASSWORD` | Bezala-lösenord |
| `BEZALA_API_URL` | Bezala API (default `https://api.bezala.com`) |
| `GMAIL_CLIENT_ID` | Från Google Cloud OAuth |
| `GMAIL_CLIENT_SECRET` | Från Google Cloud OAuth |
| `GMAIL_REFRESH_TOKEN` | Från `scripts/generate_token.py` |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | Hela service-account JSON som sträng |
| `GOOGLE_DRIVE_FOLDER_ID` | Drive-mappens ID |
| `DATABASE_URL` | **Sätts automatiskt av Railway PostgreSQL** |
| `SCAN_INTERVAL_MINUTES` | Default 60 |
| `SCAN_ENABLED` | `true`/`false` |
| `LOG_LEVEL` | `INFO` / `DEBUG` |

## Deployment till Railway

1. Skapa ett Railway-projekt och koppla detta GitHub-repo.
2. Lägg till **PostgreSQL-plugin** — `DATABASE_URL` sätts automatiskt.
3. Klistra in alla variabler ovan under **Variables**.
4. Pusha till `main` (eller annan deploy-branch). Railway använder
   `nixpacks.toml` som:
   - installerar Python- och Node-deps
   - kör `npm run build` i `frontend/`
   - startar `uvicorn app.main:app`
5. Healthcheck på `/health` bekräftar att tjänsten är uppe.

## API-endpoints

- `GET /health` — hälsokoll
- `GET /api/stats` — statistik + senaste scanning
- `GET /api/messages?limit=50` — bearbetade mail
- `GET /api/runs?limit=20` — scanning-körningar
- `POST /api/scan` — triggar manuell scanning (async)
- `GET /` — React-dashboard (om `frontend/dist/` finns)

## Dubblettskydd — detaljer

Pipeline kontrollerar i denna ordning:

1. `message_id` redan i `processed_messages`? → skip.
2. Inga giltiga PDF-bilagor? → etikettera som klart, logga som `skipped:no_pdf`.
3. `(filnamn, datum)` redan i `saved_files`? → skip.
4. Filnamn finns redan i Drive-mappen? → skip.
5. Annars: ladda upp, logga, sätt etiketten `Bezala-Klar`.

Vid race conditions fångar `UniqueConstraint` dubletter på DB-nivå.

## Felsökning

- **`Gmail OAuth saknar konfiguration`** → saknar `GMAIL_CLIENT_ID/SECRET/REFRESH_TOKEN`.
- **`GOOGLE_SERVICE_ACCOUNT_JSON är inte giltig JSON`** → kolla att du klistrat
  in hela JSON-filen utan radbrytningar som brutit tecken.
- **Inga mail hittas** → testa query manuellt i Gmail:
  `has:attachment -category:promotions -label:"Bezala-Klar"`.
- **Claude-namngivning saknas** → `ANTHROPIC_API_KEY` saknas eller felaktig;
  appen faller tillbaka på auto-genererat namn.
