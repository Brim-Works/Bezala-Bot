"""Generera en Gmail OAuth refresh_token lokalt.

Körs EN gång på din lokala maskin. Refresh-tokenen läggs sedan in som
miljövariabel `GMAIL_REFRESH_TOKEN` i Railway. Gör så här:

1. Skapa OAuth-credentials i Google Cloud Console
   (Desktop app-typ). Ladda ner som `gmail_credentials.json`
   och lägg i projektroten.
2. Kör:  python scripts/generate_token.py
3. Webbläsaren öppnas — logga in och godkänn.
4. Skriptet skriver ut CLIENT_ID, CLIENT_SECRET och REFRESH_TOKEN.
   Kopiera dessa till Railway-miljövariablerna.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.readonly",
]

CREDENTIALS_FILE = Path("gmail_credentials.json")


def main() -> int:
    if not CREDENTIALS_FILE.exists():
        print(
            f"FEL: Hittar inte {CREDENTIALS_FILE}. Ladda ner OAuth-credentials "
            "(Desktop app) från Google Cloud Console och spara i projektroten.",
            file=sys.stderr,
        )
        return 1

    flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_FILE), SCOPES)
    creds = flow.run_local_server(port=0)

    with CREDENTIALS_FILE.open() as fh:
        raw = json.load(fh)
    client_conf = raw.get("installed") or raw.get("web") or {}

    print("\n=== Railway-miljövariabler ===")
    print(f"GMAIL_CLIENT_ID={client_conf.get('client_id', '')}")
    print(f"GMAIL_CLIENT_SECRET={client_conf.get('client_secret', '')}")
    print(f"GMAIL_REFRESH_TOKEN={creds.refresh_token}")
    print("================================\n")
    print("Kopiera dessa tre värden till Railway-dashboarden.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
