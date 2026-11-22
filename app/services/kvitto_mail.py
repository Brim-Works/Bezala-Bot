"""C47 Del 2 — kvitton via mail till +kvitto-adressen.

Mail till jobbkontots "+kvitto"-adress (To/Cc/Delivered-To/X-Original-To)
med bild- eller PDF-bilaga = kvitto, oavsett de vanliga avsändarfiltren.

Adresserna står inte i koden: de läses från de anslutna Gmail-kontona.
* +kvitto-adressen = jobbkontots adress med taggen "+kvitto" (med eller
  utan punkt i namnet), eller KVITTO_ADDRESS om den är satt.
* Godkända avsändare = jobbkontots och det privata Gmail-kontots egna
  adresser (+ ev. KVITTO_ALLOWED_SENDERS). Allt annat ignoreras och det
  loggas bara att mailet avvisades.

Samma kedja som mobilen (receipt_inbox), källa 'mail_kvitto'. Flera
bilagor = flera kvitton (message_id, message_id#2 …).
"""

from __future__ import annotations

import logging
import os
from email.utils import getaddresses, parseaddr

from app.db import session_scope
from app.models import ProcessedMessage

logger = logging.getLogger(__name__)

TAG = "kvitto"
HEADERS = ["From", "To", "Cc", "Delivered-To", "X-Original-To", "Authentication-Results"]

_rejected_logged: set[str] = set()
_private_profile: list[str] = []


def normalize(address: str) -> str:
    """Gemener; gmail.com: punkter och +tagg ignoreras (Gmail gör så)."""
    addr = (address or "").strip().lower()
    local, _, domain = addr.partition("@")
    if domain in ("gmail.com", "googlemail.com"):
        local = local.split("+", 1)[0].replace(".", "")
        domain = "gmail.com"
    return f"{local}@{domain}"


def _base(address: str) -> tuple[str, str, str]:
    """(namn utan punkter, tagg, domän)."""
    local, _, domain = (address or "").strip().lower().partition("@")
    name, _, tag = local.partition("+")
    return name.replace(".", ""), tag, domain


class KvittoConfig:
    def __init__(self, work_address: str, own_addresses: list[str]):
        self.work_address = (work_address or "").strip().lower()
        # Jämförs utan punkter och +tagg (namn.efternamn = namnefternamn).
        self.own = {_base(a)[0] + "@" + _base(a)[2] for a in own_addresses if a and "@" in a}
        explicit = os.environ.get("KVITTO_ADDRESS", "").strip().lower()
        self.explicit = explicit or None

    def is_plus(self, address: str) -> bool:
        address = (address or "").strip().lower()
        if self.explicit:
            return address == self.explicit
        name, tag, domain = _base(address)
        wname, _, wdomain = _base(self.work_address)
        return bool(wname) and tag == TAG and name == wname and domain == wdomain

    def query(self) -> str:
        if self.explicit:
            targets = [self.explicit]
        else:
            local, _, domain = self.work_address.partition("@")
            local = local.split("+", 1)[0]
            variants = {local, local.replace(".", "")}
            targets = sorted(f"{v}+{TAG}@{domain}" for v in variants)
        ors = " OR ".join(f"to:{t} OR deliveredto:{t}" for t in targets)
        return f"{{{ors}}} has:attachment -in:spam -in:trash newer_than:30d"

    def is_own_sender(self, from_header: str) -> bool:
        name, _, domain = _base(normalize(parseaddr(from_header or "")[1]))
        return bool(name) and f"{name}@{domain}" in self.own


def _private_profile_email() -> str | None:
    if _private_profile:
        return _private_profile[0] or None
    try:
        from app.services.gmail_client import ACCOUNT_PRIVATE, GmailClient

        email = GmailClient(account=ACCOUNT_PRIVATE).get_profile_email()
    except Exception:  # noqa: BLE001 — privat Gmail kan vara frånkopplat
        email = ""
    _private_profile.append(email)
    return email or None


_last_config: list[KvittoConfig] = []


def build_config(gmail) -> KvittoConfig:
    work = gmail.get_profile_email()
    own = [work, _private_profile_email() or ""]
    own += [a for a in os.environ.get("KVITTO_ALLOWED_SENDERS", "").split(",") if a.strip()]
    cfg = KvittoConfig(work, own)
    cfg.own_addresses = [a.strip().lower() for a in own if a and "@" in a]
    _last_config[:] = [cfg]
    return cfg


def plus_address(cfg: KvittoConfig) -> str | None:
    """Adressen Mikko mailar kvitton till (inte hemlig)."""
    if cfg.explicit:
        return cfg.explicit
    local, _, domain = cfg.work_address.partition("@")
    if not local or not domain:
        return None
    return f"{local.split('+', 1)[0]}+{TAG}@{domain}"


def _variants(cfg: KvittoConfig) -> list[str]:
    """Alla stavningar som godkänns (med och utan punkt i namnet)."""
    if cfg.explicit:
        return [cfg.explicit]
    local, _, domain = cfg.work_address.partition("@")
    local = local.split("+", 1)[0]
    return sorted({f"{v}+{TAG}@{domain}" for v in (local, local.replace(".", ""))})


def _log_mail(mid: str, sender: str | None, received_at, attachments: int | None,
              outcome: str, reason: str | None = None) -> None:
    from app.models import KvittoMailLog

    try:
        with session_scope() as db:
            if db.query(KvittoMailLog.id).filter(KvittoMailLog.message_id == mid).first():
                return
            db.add(KvittoMailLog(message_id=mid, sender=(sender or "")[:512] or None,
                                 received_at=received_at, attachments=attachments,
                                 outcome=outcome, reason=(reason or None) and reason[:200]))
    except Exception:  # noqa: BLE001 — loggen får aldrig fälla passet
        logger.exception("[c48-kvitto] kunde inte logga %s", mid)


def inbox_info(db, *, gmail=None, limit: int = 10) -> dict:
    """C48 Del 5: +kvitto-adressen, godkända avsändare och senaste mail."""
    from app.models import KvittoMailLog

    cfg = _last_config[0] if _last_config else None
    error = None
    if cfg is None:
        try:
            if gmail is None:
                from app.services.gmail_client import GmailClient

                gmail = GmailClient()
            cfg = build_config(gmail)
        except Exception as exc:  # noqa: BLE001
            error = f"Gmail not reachable: {exc}"[:300]
    rows = (db.query(KvittoMailLog).order_by(KvittoMailLog.id.desc())
            .limit(max(1, min(int(limit or 10), 50))).all())
    out = {
        "address": plus_address(cfg) if cfg else None,
        "also_accepted": _variants(cfg) if cfg else [],
        "allowed_senders": list(getattr(cfg, "own_addresses", []) or []) if cfg else [],
        "note": ("Mail with a photo or PDF to this address from one of the allowed "
                 "senders becomes a receipt (one per attachment). Other senders are "
                 "only logged as rejected."),
        "recent": [{
            "at": (r.received_at or r.created_at).isoformat() if (r.received_at or r.created_at) else None,
            "sender": r.sender, "attachments": r.attachments, "outcome": r.outcome,
            "reason": r.reason,
        } for r in rows],
    }
    if error:
        out["error"] = error
    return out


def addressed_to_plus(cfg: KvittoConfig, headers: dict[str, list[str]]) -> bool:
    values = []
    for name in ("to", "cc", "delivered-to", "x-original-to"):
        values += headers.get(name, [])
    return any(cfg.is_plus(addr) for _, addr in getaddresses(values))


def auth_failed(headers: dict[str, list[str]]) -> bool:
    res = " ".join(headers.get("authentication-results", [])).lower()
    return "dmarc=fail" in res or "spf=fail" in res


def receipt_attachments(msg) -> list:
    from app.services.inbox_helpers import is_receipt_file

    return [a for a in msg.attachments if is_receipt_file(a.filename, a.mime_type, a.data)]


def _seen(mid: str) -> bool:
    with session_scope() as db:
        return db.query(ProcessedMessage.id).filter(
            ProcessedMessage.message_id == mid).first() is not None


def run_kvitto_pass(gmail, *, drive=None, analyzer=None, max_results: int = 25,
                    config: KvittoConfig | None = None) -> dict:
    """Körs i varje scanning (jobbkontot). Idempotent per Gmail-id."""
    from app.services.receipt_inbox import SOURCE_MAIL_KVITTO, InboxError, ingest_receipt_file

    out = {"found": 0, "saved": 0, "rejected": 0, "skipped": 0}
    try:
        cfg = config or build_config(gmail)
        ids = gmail.list_candidate_message_ids(query=cfg.query(), max_results=max_results)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[c47-kvitto] Gmail-sökning misslyckades: %s", exc)
        return {**out, "error": str(exc)}
    out["found"] = len(ids)
    for mid in ids:
        if _seen(mid):
            out["skipped"] += 1
            continue
        try:
            headers = gmail.fetch_headers(mid, HEADERS)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[c47-kvitto] headers för %s misslyckades: %s", mid, exc)
            continue
        if not addressed_to_plus(cfg, headers):
            out["skipped"] += 1
            continue
        frm = (headers.get("from") or [""])[0]
        if not cfg.is_own_sender(frm) or auth_failed(headers):
            out["rejected"] += 1
            _log_mail(mid, parseaddr(frm)[1] or frm, None, None, "rejected",
                      "sender authentication failed" if cfg.is_own_sender(frm)
                      else "sender is not one of Mikko's own addresses")
            if mid not in _rejected_logged:
                _rejected_logged.add(mid)
                logger.info("[c47-kvitto] avvisat mail %s (avsändaren är inte Mikkos egen)", mid)
            continue
        msg = gmail.fetch_message(mid)
        atts = receipt_attachments(msg)
        if not atts:
            logger.info("[c47-kvitto] %s saknar bild/PDF-bilaga - hoppar", mid)
            out["skipped"] += 1
            _log_mail(mid, msg.sender, msg.received_at, 0, "no_attachment",
                      "no photo or PDF attachment")
            continue
        _log_mail(mid, msg.sender, msg.received_at, len(atts), "accepted")
        for i, att in enumerate(atts):
            rid = mid if i == 0 else f"{mid}#{i + 1}"
            try:
                with session_scope() as db:
                    res = ingest_receipt_file(
                        db, att.data, filename=att.filename, declared_mime=att.mime_type,
                        source=SOURCE_MAIL_KVITTO, sender=msg.sender, subject=msg.subject,
                        received_at=msg.received_at, message_id=rid,
                        thread_id=msg.thread_id, analyzer=analyzer, drive=drive,
                    )
                    if res.created:
                        out["saved"] += 1
            except InboxError as exc:
                logger.info("[c47-kvitto] %s bilaga %d: %s", mid, i + 1, exc)
            except Exception:  # noqa: BLE001
                logger.exception("[c47-kvitto] %s bilaga %d misslyckades", mid, i + 1)
        try:
            gmail.mark_done(mid)
        except Exception:  # noqa: BLE001
            logger.warning("[c47-kvitto] kunde inte sätta etikett på %s", mid)
    logger.info("[c47-kvitto] %s", out)
    return out
