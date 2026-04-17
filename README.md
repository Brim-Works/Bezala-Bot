# Bezala Bot

Scannar Gmail automatiskt, hittar kvitton och resedokument, namnger dem med Claude AI
och sparar till Google Drive. Allt loggas i PostgreSQL. Deployas på Railway.

## Stack
- **Backend:** FastAPI + APScheduler (Python 3.11)
- **Frontend:** React + Vite (läggs till i Fas 6)
- **Databas:** PostgreSQL (Railway)
- **Integrationer:** Gmail API, Google Drive, Claude AI, Bezala API

## Projektstruktur

```
bezala-bot/
├── app/
│   ├── main.py         # FastAPI entrypoint
│   ├── config.py       # Miljövariabler
│   ├── db.py           # SQLAlchemy engine/session
│   └── models.py       # DB-modeller
├── scripts/
│   └── generate_token.py  # Lokal Gmail OAuth-helper
├── requirements.txt
├── Procfile
├── railway.toml
├── nixpacks.toml
├── .env.example
└── README.md
```

## Lokal utveckling

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fyll i värden
uvicorn app.main:app --reload
```

Hälsokoll: `curl http://localhost:8000/health`

## Gmail OAuth — engångssetup

Gmail kräver en `refresh_token` som genereras en gång lokalt och sedan läggs in
som miljövariabel i Railway.

1. Skapa ett projekt i [Google Cloud Console](https://console.cloud.google.com/).
2. Aktivera **Gmail API**.
3. Skapa OAuth-credentials av typen **Desktop app**.
4. Ladda ner JSON-filen och spara som `gmail_credentials.json` i projektroten.
5. Kör:

   ```bash
   python scripts/generate_token.py
   ```

6. Logga in i webbläsaren som öppnas. Skriptet skriver ut:
   - `GMAIL_CLIENT_ID`
   - `GMAIL_CLIENT_SECRET`
   - `GMAIL_REFRESH_TOKEN`
7. Klistra in dessa i Railway → Variables.

> `gmail_credentials.json` finns i `.gitignore` och checkas aldrig in.

## Google Drive — service account

1. Google Cloud Console → IAM → Service Accounts → skapa konto.
2. Skapa nyckel (JSON) och ladda ner.
3. Öppna JSON-filen, kopiera HELA innehållet som en rad.
4. Klistra in som `GOOGLE_SERVICE_ACCOUNT_JSON` i Railway.
5. Dela din Drive-mapp med service account-e-posten (Editor-behörighet).

## Railway — miljövariabler

| Variabel | Beskrivning |
| --- | --- |
| `ANTHROPIC_API_KEY` | Claude API-nyckel |
| `BEZALA_USERNAME` | Bezala-användarnamn |
| `BEZALA_PASSWORD` | Bezala-lösenord |
| `GMAIL_CLIENT_ID` | Från Google Cloud OAuth |
| `GMAIL_CLIENT_SECRET` | Från Google Cloud OAuth |
| `GMAIL_REFRESH_TOKEN` | Från `generate_token.py` |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | Hela service-account JSON som sträng |
| `GOOGLE_DRIVE_FOLDER_ID` | ID för Drive-mappen att spara till |
| `DATABASE_URL` | Sätts automatiskt av Railway PostgreSQL |
| `SCAN_INTERVAL_MINUTES` | Default 60 |
| `SCAN_ENABLED` | `true`/`false` |

## Deployment

```bash
railway login
railway link        # koppla till projektet
railway up          # eller pusha till GitHub och låt Railway auto-deploya
```

Lägg till PostgreSQL-plugin i Railway-projektet — `DATABASE_URL` sätts automatiskt.

## API-endpoints (hittills)

- `GET /health` — hälsokoll
- `GET /api/messages?limit=50` — lista bearbetade meddelanden
- `GET /api/stats` — statistik + senaste scanning

Scanning-endpoints, schemaläggare och frontend läggs till i kommande faser.
