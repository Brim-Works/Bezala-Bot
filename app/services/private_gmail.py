"""C45 — privat Gmail (mikko.keinonen@gmail.com), bara kvitton.

Minsta möjliga åtkomst:
  * OAuth-scope `gmail.readonly` — boten kan inte ändra, etikettera eller
    skicka något i den privata brevlådan.
  * Gmail-frågan byggs ENBART av aktiva rader i `private_gmail_senders`
    (C46; `PRIVATE_GMAIL_SENDER_ALLOWLIST` lägger bara till saknade vid start):
    `from:(a OR b) after:…`. Inga andra sökord, inga andra avsändare.
  * Varje meddelande kontrolleras igen mot allowlisten via ett metadata-anrop
    (bara From-headern) INNAN innehållet hämtas. Ett meddelande från en annan
    avsändare som ändå råkar returneras hämtas aldrig i sin helhet, lagras
    inte, loggas inte (bara ett anonymt id-räkneverk) och skickas aldrig
    till AI-analys.
"""

from __future__ import annotations

import re
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


# C46 — publika e-posttjänster: hela domänen får aldrig läggas till (då
# skulle boten kunna läsa vanliga privatpersoners mail).
BLOCKED_DOMAINS = (
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com",
    "icloud.com", "me.com", "yahoo.com", "proton.me", "protonmail.com",
)
_GMX_RE = re.compile(r"(^|\.)gmx\.[a-z.]+$")


class InvalidSender(ValueError):
    """Avsändaren är inte giltig eller är blockerad."""


def _domain_blocked(domain: str) -> bool:
    d = domain.lower().strip(".")
    if _GMX_RE.search(d):
        return True
    return any(d == b or d.endswith("." + b) for b in BLOCKED_DOMAINS)


_DOMAIN_RE = re.compile(r"^[a-z0-9-]+(\.[a-z0-9-]+)+$")
_LOCAL_RE = re.compile(r"^[a-z0-9._%+'-]+$")


def validate_sender(raw: str | None, *, blocked_addresses: Iterable[str] = ()) -> tuple[str, str]:
    """→ (normaliserad adress, kind). kind='email' (exakt adress) eller
    'domain' (skrivs '@domän.se', matchar också underdomäner).
    Kastar InvalidSender med en kort förklaring."""
    addr = (raw or "").strip().lower()
    if not addr or any(ch.isspace() for ch in addr) or addr.count("@") != 1:
        raise InvalidSender("Ange en e-postadress eller en domän som @domän.se")
    local, domain = addr.split("@")
    if not _DOMAIN_RE.match(domain):
        raise InvalidSender("Ogiltig domän")
    if _domain_blocked(domain):
        raise InvalidSender(f"{domain} är en publik e-posttjänst och får inte läggas till")
    if addr in {a.strip().lower() for a in blocked_addresses if a}:
        raise InvalidSender("Det privata kontots egen adress får inte läggas till")
    if not local:
        return "@" + domain, "domain"
    if not _LOCAL_RE.match(local):
        raise InvalidSender("Ogiltig e-postadress")
    return addr, "email"


def parse_allowlist(raw: str | Iterable[str] | None) -> list[str]:
    """Normalisera allowlisten: gemener, trimmad, utan dubbletter.
    Giltigt: exakt e-postadress eller '@domän.se'. Fritext, '*', bara
    'domän.se' och publika e-postdomäner (gmail.com …) tas bort."""
    if raw is None:
        return []
    items = raw.split(",") if isinstance(raw, str) else list(raw)
    out: list[str] = []
    for item in items:
        try:
            addr, _kind = validate_sender(item)
        except InvalidSender:
            continue
        if addr not in out:
            out.append(addr)
    return out


def get_private_allowlist() -> list[str]:
    """Aktiva avsändare ur tabellen private_gmail_senders (C46 — tabellen
    är sanningen). Vid DB-fel: tom lista (då läses ingenting)."""
    try:
        from app.services.private_senders import active_addresses

        return parse_allowlist(active_addresses())
    except Exception:  # noqa: BLE001
        import logging

        logging.getLogger(__name__).exception("C46: kunde inte läsa private_gmail_senders")
        return []


def env_allowlist() -> list[str]:
    return parse_allowlist(get_settings().private_gmail_sender_allowlist)


def _query_term(entry: str) -> str:
    # '@scandichotels.com' → 'scandichotels.com' (Gmail matchar då även
    # notify.scandichotels.com). Avsändarkontrollen före hämtning gäller ändå.
    return entry[1:] if entry.startswith("@") else entry


def build_private_query(allowlist: Iterable[str], *, after: str = PRIVATE_SCAN_AFTER) -> str | None:
    """`from:(a OR b) after:YYYY/MM/DD`. None om allowlisten är tom —
    då söks ingenting alls i den privata brevlådan."""
    senders = parse_allowlist(list(allowlist))
    if not senders:
        return None
    return f"from:({' OR '.join(_query_term(s) for s in senders)}) after:{after}"


def sender_matches(addr: str, entry: str) -> bool:
    """Exakt adress, eller '@domän' som matchar domänen och underdomäner."""
    if not addr or "@" not in addr:
        return False
    if entry.startswith("@"):
        dom = addr.split("@", 1)[1]
        want = entry[1:]
        return dom == want or dom.endswith("." + want)
    return addr == entry


def sender_address(from_header: str | None) -> str:
    return (parseaddr(from_header or "")[1] or "").strip().lower()


def sender_allowed(from_header: str | None, allowlist: Iterable[str]) -> bool:
    """Exakt adressmatch (skiftlägesokänslig) eller '@domän'-match
    (domänen + underdomäner) mot allowlisten."""
    addr = sender_address(from_header)
    if not addr:
        return False
    return any(sender_matches(addr, e) for e in parse_allowlist(list(allowlist)))
