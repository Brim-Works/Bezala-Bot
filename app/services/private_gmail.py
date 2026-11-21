"""C45 — privat Gmail (mikko.keinonen@gmail.com), bara kvitton.

Minsta möjliga åtkomst:
  * OAuth-scope `gmail.readonly` — boten kan inte ändra, etikettera eller
    skicka något i den privata brevlådan.
  * Gmail-frågan byggs ENBART av allowlisten (`PRIVATE_GMAIL_SENDER_ALLOWLIST`):
    `from:(a OR b) after:…`. Inga andra sökord, inga andra avsändare.
  * Varje meddelande kontrolleras igen mot allowlisten via ett metadata-anrop
    (bara From-headern) INNAN innehållet hämtas. Ett meddelande från en annan
    avsändare som ändå råkar returneras hämtas aldrig i sin helhet, lagras
    inte, loggas inte (bara ett anonymt id-räkneverk) och skickas aldrig
    till AI-analys.
"""

from __future__ import annotations

from email.utils import parseaddr
from typing import Iterable

from app.config import get_settings

ACCOUNT_VISMA = "visma"
ACCOUNT_PRIVATE = "private"
ACCOUNTS = (ACCOUNT_VISMA, ACCOUNT_PRIVATE)

# oauth_tokens.service-nyckel per konto. Visma-kontot behåller sin gamla
# nyckel 'gmail' så befintlig token (och Calendar) fortsätter exakt som förr.
TOKEN_SERVICE = {
    ACCOUNT_VISMA: "gmail",
    ACCOUNT_PRIVATE: "gmail_private",
}

PRIVATE_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

# Privata kvitton hämtas från och med 2026 (kortraderna som ska matchas).
PRIVATE_SCAN_AFTER = "2026/01/01"


class PrivateSenderNotAllowed(Exception):
    """Meddelandet i den privata brevlådan kommer inte från en allowlistad
    avsändare. Innehållet har INTE hämtats."""


def parse_allowlist(raw: str | Iterable[str] | None) -> list[str]:
    """Normalisera allowlisten: gemener, trimmad, utan dubbletter.
    Bara fullständiga e-postadresser accepteras (måste innehålla '@' och
    en lokal del) — ett domän- eller fritextmönster skulle vidga åtkomsten."""
    if raw is None:
        return []
    items = raw.split(",") if isinstance(raw, str) else list(raw)
    out: list[str] = []
    for item in items:
        addr = (item or "").strip().lower()
        if not addr or " " in addr or addr.count("@") != 1:
            continue
        local, domain = addr.split("@")
        if not local or "." not in domain:
            continue
        if addr not in out:
            out.append(addr)
    return out


def get_private_allowlist() -> list[str]:
    return parse_allowlist(get_settings().private_gmail_sender_allowlist)


def build_private_query(allowlist: Iterable[str], *, after: str = PRIVATE_SCAN_AFTER) -> str | None:
    """`from:(a OR b) after:YYYY/MM/DD`. None om allowlisten är tom —
    då söks ingenting alls i den privata brevlådan."""
    senders = parse_allowlist(list(allowlist))
    if not senders:
        return None
    return f"from:({' OR '.join(senders)}) after:{after}"


def sender_address(from_header: str | None) -> str:
    return (parseaddr(from_header or "")[1] or "").strip().lower()


def sender_allowed(from_header: str | None, allowlist: Iterable[str]) -> bool:
    """Exakt adressmatch (skiftlägesokänslig) mot allowlisten."""
    addr = sender_address(from_header)
    return bool(addr) and addr in set(parse_allowlist(list(allowlist)))
