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

## Engångssetup — Google OAuth (två konton)

Gmail-scanning körs mot ett **Visma-konto** medan Drive-uppladdning sker mot
ett **privat Gmail-konto**. Anledningen: Visma Workspace blockerar extern
delning av Drive-mappar till tredje-parts OAuth-klienter, så mappen där
kvitton sparas måste ligga i ett privat Drive.

Samma OAuth-klient (CLIENT_ID/SECRET) används i båda fallen — bara två olika
användare godkänner. Du får **två separata refresh-tokens**:

| Token | Konto | Scopes |
| --- | --- | --- |
| `GMAIL_REFRESH_TOKEN` | Visma-kontot | `gmail.modify`, `gmail.readonly`, `gmail.labels` |
| `DRIVE_REFRESH_TOKEN` | Privat Gmail | `drive.file`, `drive.readonly` |

`drive.file` ger appen access endast till filer den själv skapar. `drive.readonly`
läggs till så att dubblettkontrollen kan se befintliga filer i mappen även om
de inte laddats upp av appen.

1. Skapa projekt i [Google Cloud Console](https://console.cloud.google.com/)
   (gör detta i det privata Google-kontot — där Drive-mappen ligger).
2. Aktivera **Gmail API** och **Google Drive API**.
3. Skapa OAuth-credentials av typen **Desktop app**.
4. Ladda ner JSON-filen och spara som `gmail_credentials.json` i projektroten.
5. Generera Gmail-tokenen (Visma-kontot):

   ```bash
   python scripts/generate_token.py --target gmail
   ```

   Logga in med **Visma-kontot** i webbläsaren som öppnas. Skriptet skriver ut
   `GMAIL_CLIENT_ID`, `GMAIL_CLIENT_SECRET` och `GMAIL_REFRESH_TOKEN`.

6. Generera Drive-tokenen (privata kontot):

   ```bash
   python scripts/generate_token.py --target drive
   ```

   Logga in med **privata Gmail-kontot** (samma som äger Drive-mappen).
   Skriptet skriver ut `DRIVE_REFRESH_TOKEN`.

7. Klistra in alla fyra värden i Railway → **Variables**
   (`GMAIL_CLIENT_ID` och `GMAIL_CLIENT_SECRET` är samma för båda).
8. Sätt `GOOGLE_DRIVE_FOLDER_ID` till ID:t på Drive-mappen i det privata
   kontot (syns i URL:en när du öppnar mappen).

> `gmail_credentials.json` finns i `.gitignore` och checkas aldrig in.

> OAuth-klienten i Google Cloud Console måste ha både Visma- och privatkontots
> e-post listade under **OAuth consent screen → Test users** (om appen är i
> testläge).

## Railway — miljövariabler

| Variabel | Beskrivning |
| --- | --- |
| `ANTHROPIC_API_KEY` | Claude API-nyckel |
| `BEZALA_USERNAME` | Bezala-användarnamn |
| `BEZALA_PASSWORD` | Bezala-lösenord |
| `BEZALA_API_URL` | Bezala API (default `https://api.bezala.com`) |
| `GMAIL_CLIENT_ID` | OAuth-klient (samma för Gmail + Drive) |
| `GMAIL_CLIENT_SECRET` | OAuth-klient (samma för Gmail + Drive) |
| `GMAIL_REFRESH_TOKEN` | Visma-kontots token — `python scripts/generate_token.py --target gmail` |
| `DRIVE_REFRESH_TOKEN` | Privata kontots token — `python scripts/generate_token.py --target drive` |
| `GOOGLE_DRIVE_FOLDER_ID` | Drive-mappens ID (default: `1FoK-nmaDLgIUnMUImECjxXBO9XqLgFZb`) |
| `DATABASE_URL` | **Sätts automatiskt av Railway PostgreSQL** |
| `SCAN_INTERVAL_MINUTES` | Default 60 |
| `SCAN_ENABLED` | `true`/`false` |
| `LOG_LEVEL` | `INFO` / `DEBUG` |
| `APP_PASSWORD` | Lösenord för att logga in i dashboarden |
| `SESSION_SECRET` | Hemlighet för signering av session-cookies. Generera med `python3 -c "import secrets; print(secrets.token_hex(32))"` |

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

## Autentisering

Hela dashboarden är lösenordsskyddad.

- `GET /login` — inloggningsformulär
- `POST /login` — validerar lösenord (från `APP_PASSWORD`) och sätter
  signerad session-cookie (`httpOnly`, `secure`, `samesite=lax`)
- `POST /logout` — rensar sessionen
- `GET /api/me` — returnerar `{"authenticated": true}` om inloggad, annars 401

Samtliga `/api/*`-endpoints kräver giltig session och svarar `401 Unauthorized`
utan. Frontend redirectar automatiskt till `/login` vid 401.

Sätt `APP_PASSWORD` och `SESSION_SECRET` som miljövariabler i Railway.
Glömmer du `SESSION_SECRET` genererar appen en tillfällig hemlighet vid
uppstart — men då logggas alla ut vid varje deploy.

## API-endpoints

- `GET /health` — hälsokoll (öppen, ingen auth)
- `GET /login` — inloggningssida (öppen)
- `POST /login` — logga in med lösenord (öppen)
- `POST /logout` — logga ut
- `GET /api/me` — auth-status (401 om inte inloggad)
- `GET /api/stats` — statistik + senaste scanning *(auth)*
- `GET /api/messages?limit=50` — bearbetade mail *(auth)*
- `GET /api/runs?limit=20` — scanning-körningar *(auth)*
- `POST /api/scan` — triggar manuell scanning (async) *(auth)*
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

- **`Gmail OAuth saknar konfiguration`** → saknar `GMAIL_CLIENT_ID/SECRET` eller `GMAIL_REFRESH_TOKEN`.
- **`Drive OAuth saknar konfiguration`** → saknar `DRIVE_REFRESH_TOKEN`, eller
  tokenen är genererad med fel konto / utan scopen `drive.file` + `drive.readonly`.
  Regenerera med `python scripts/generate_token.py --target drive`.
- **`insufficientScopes` från Drive-API** → refresh-tokenen saknar `drive.file`
  eller `drive.readonly`. Generera om och godkänn alla scopes i webbläsaren.
- **`storageQuotaExceeded` vid Drive-upload** → Drive-tokenen är skapad med ett
  Visma/Workspace-konto som saknar kvot eller blockerar extern delning. Generera
  Drive-tokenen mot ditt privata Gmail istället.
- **Inga mail hittas** → testa query manuellt i Gmail:
  `has:attachment -category:promotions -label:"Bezala-Klar"`.
- **Claude-namngivning saknas** → `ANTHROPIC_API_KEY` saknas eller felaktig;
  appen faller tillbaka på auto-genererat namn.
