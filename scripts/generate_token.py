"""Generera OAuth refresh_token för Gmail- eller Drive-kontot.

Eftersom Visma Workspace blockerar externa Drive-delningar måste Gmail- och
Drive-åtkomsten gå via två olika konton. Samma OAuth-klient används i
båda fallen — bara användaren som godkänner är olika.

Användning:
    python scripts/generate_token.py --target gmail
    python scripts/generate_token.py --target drive

Kör skriptet EN gång per konto. Skriv ut-värdena läggs sedan in som
miljövariabler i Railway:
  --target gmail → GMAIL_REFRESH_TOKEN
  --target drive → DRIVE_REFRESH_TOKEN
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.labels",
]

DRIVE_SCOPES = [
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/drive.readonly",
]

CREDENTIALS_FILE = Path("gmail_credentials.json")

ENV_VAR = {
    "gmail": "GMAIL_REFRESH_TOKEN",
    "drive": "DRIVE_REFRESH_TOKEN",
}

SCOPES_FOR = {
    "gmail": GMAIL_SCOPES,
    "drive": DRIVE_SCOPES,
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--target",
        required=True,
        choices=("gmail", "drive"),
        help="Vilket konto tokenen är för (gmail = Visma, drive = privat).",
    )
    args = parser.parse_args()

    if not CREDENTIALS_FILE.exists():
        print(
            f"FEL: Hittar inte {CREDENTIALS_FILE}. Ladda ner OAuth-credentials "
            "(Desktop app) från Google Cloud Console och spara i projektroten.",
            file=sys.stderr,
        )
        return 1

    scopes = SCOPES_FOR[args.target]
    env_var = ENV_VAR[args.target]

    print(f"\nStartar OAuth-flöde för {args.target.upper()}-kontot.")
    print("Logga in med rätt Google-konto i webbläsaren som öppnas.\n")

    flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_FILE), scopes)
    creds = flow.run_local_server(port=0)

    with CREDENTIALS_FILE.open() as fh:
        raw = json.load(fh)
    client_conf = raw.get("installed") or raw.get("web") or {}

    print("\n=== Railway-miljövariabler ===")
    print(f"GMAIL_CLIENT_ID={client_conf.get('client_id', '')}")
    print(f"GMAIL_CLIENT_SECRET={client_conf.get('client_secret', '')}")
    print(f"{env_var}={creds.refresh_token}")
    print("================================\n")
    print(
        "CLIENT_ID/SECRET är samma för båda tokens — sätt en gång. "
        f"Kopiera {env_var} separat."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
