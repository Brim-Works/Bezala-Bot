#!/usr/bin/env python3
"""FAS A — utforskningsscript: testa vilken metod Bezala accepterar för
att sätta metadata på en bill_line i match-to-bezala-flödet.

Bakgrund: I commit 31d7317 togs metadata-byggning bort från match-flödet
med motivering "kortransaktioner är read-only, PUT ger 403". Men det
gällde transaction_id, INTE bill_line_id (resursen byttes i 5e12982).
PUT-blockeringen är aldrig korrekt verifierad efter resurs-bytet.

Tre kandidat-metoder, körs en i taget:

  A1: POST /attachments med nested bill_line[vat_lines_attributes][0][...]
      i SAMMA multipart-form som filen + draft=1 + bill_line_id.
      Fördel: ett HTTP-anrop. Nackdel: oklart om Bezala parsar nested.

  A2: PUT /bill_lines/{id} med JSON-body {"bill_line": {...}}
      Separat steg INNAN POST /attachments.
      Fördel: ren REST. Nackdel: 403 om "read-only"-teorin stämmer.

  A3: PATCH /bill_lines/{id} med samma JSON-body som A2.
      Vissa Rails-API:er skiljer på PUT (full replace) och PATCH (delta).

VARNING — DETTA SCRIPT MUTERAR PRODUKTIONSDATA I BEZALA.

  - A1 bifogar en (tom 1-sids-PDF) fil till bill_line. Filen syns i
    Bezala-UI:t tills någon raderar den.
  - A2/A3 muterar metadata på bill_line (expense_account, vat_code,
    cost_center). Om bill_line redan har metadata: SKRIVS ÖVER.

Använd endast med bill_line_id:n du är beredd att städa upp efter,
helst i en separat test-organisation eller ett test-konto.

ANVÄNDNING

  # Steg 1: hitta en lämplig bill_line_id att testa mot
  python scripts/list_bezala_metadata.py --only accounts  # bekräfta env
  # eller titta i Bezala-UI:t på en kortrad utan kvitto.

  # Steg 2: kör en metod i taget
  python scripts/test_match_flow_strategies.py \
      --method a1 --bill-line-id 1234567

  # Default-värden för account/cost-center/vat-code är Mikkos prod-värden:
  #   expense_account_id = 67100   (Matkaliput)
  #   cost_center_ids    = [927151] (VIS128 Visma HRM Sverige AB)
  #   vat_code_id        = 1355     (FI 25,5%)
  #   tax_percentage     = 0.255
  #   amount             = 10.00
  #   currency           = EUR

  # Overrida vid behov:
  python scripts/test_match_flow_strategies.py \
      --method a2 --bill-line-id 1234567 \
      --account-id 67102 --amount 250.50 --currency SEK

EXIT-KODER

  0   metoden returnerade 2xx (status_code skrivs i loggen)
  1   metoden returnerade non-2xx (status + body skrivs i loggen)
  2   uppstart-fel (env saknas, BezalaClient kunde inte initieras, etc.)
"""

from __future__ import annotations

import argparse
import logging
import os
import sys


# Minimal 1-sids PDF (krävs av A1 + av BezalaClients PDF-guard som
# verifierar startwith(b"%PDF")). Inte vacker, men giltig.
_MINIMAL_PDF: bytes = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Count 1/Kids[3 0 R]>>endobj\n"
    b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]"
    b"/Resources<<>>>>endobj\n"
    b"xref\n0 4\n"
    b"0000000000 65535 f \n"
    b"0000000010 00000 n \n"
    b"0000000053 00000 n \n"
    b"0000000099 00000 n \n"
    b"trailer<</Size 4/Root 1 0 R>>\nstartxref\n173\n%%EOF\n"
)


def _setup_path() -> None:
    """Säkerställ att app/ är importerbar oavsett CWD."""
    here = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.dirname(here)
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


def _build_vat_line_form_fields(
    *,
    expense_account_id: int,
    cost_center_id: int,
    vat_code_id: int,
    tax_percentage: str,
    taxable: str,
    currency: str,
) -> dict[str, str]:
    """A1 form-mönster: Rails-style nested params i multipart-form.

    Bezala-controllern förväntar sig fält som
    `bill_line[vat_lines_attributes][0][expense_account_id]` enligt
    historisk c8196f2-implementation.
    """
    prefix = "bill_line[vat_lines_attributes][0]"
    return {
        f"{prefix}[expense_account_id]": str(expense_account_id),
        f"{prefix}[cost_center_ids][]": str(cost_center_id),
        f"{prefix}[vat_code_id]": str(vat_code_id),
        f"{prefix}[tax_percentage]": tax_percentage,
        f"{prefix}[taxable]": taxable,
        f"{prefix}[currency]": currency,
    }


def _build_vat_line_json(
    *,
    expense_account_id: int,
    cost_center_id: int,
    vat_code_id: int,
    tax_percentage: str,
    taxable: str,
    currency: str,
) -> dict:
    """A2/A3 JSON-body. Bezala API-konvention: top-level wrapper-key
    (`bill_line`) som innehåller fält att uppdatera, här bara
    `vat_lines_attributes`."""
    return {
        "bill_line": {
            "vat_lines_attributes": [
                {
                    "expense_account_id": expense_account_id,
                    "cost_center_ids": [cost_center_id],
                    "vat_code_id": vat_code_id,
                    "tax_percentage": tax_percentage,
                    "taxable": taxable,
                    "currency": currency,
                }
            ]
        }
    }


def _log_outcome(method_label: str, status_code: int, body: str) -> int:
    """Skriver en sammanfattning + returnerar 0 om 2xx, 1 annars."""
    ok = 200 <= status_code < 300
    verdict = "FUNGERADE ✅" if ok else "FAILADE ❌"
    print("\n" + "=" * 70)
    print(f"METOD {method_label} — {verdict}  (HTTP {status_code})")
    print("=" * 70)
    print(f"Bezala-respons (body, första 4000 tecken):\n{body}")
    print("=" * 70)
    return 0 if ok else 1


def _run_a1(
    client, *, bill_line_id: str, vat_form: dict[str, str],
    description: str,
) -> int:
    """POST /attachments multipart med nested bill_line[...]-fält."""
    from app.services.bezala_client import FILE_FIELD_NAME, _safe_body_snippet

    form: dict[str, str] = {
        "draft": "1",
        "bill_line_id": bill_line_id,
        "description": description,
    }
    form.update(vat_form)

    print(f"\n→ A1: POST /attachments  multipart-keys={sorted(form.keys())}")
    resp = client._request(
        "POST",
        "/attachments",
        files={FILE_FIELD_NAME: (
            "test_match_flow_strategies.pdf",
            _MINIMAL_PDF,
            "application/pdf",
        )},
        data=form,
    )
    return _log_outcome("A1 (POST /attachments + nested)", resp.status_code,
                        _safe_body_snippet(resp))


def _run_a2(client, *, bill_line_id: str, body: dict) -> int:
    """PUT /bill_lines/{id} med JSON-body."""
    from app.services.bezala_client import _safe_body_snippet

    print(f"\n→ A2: PUT /bill_lines/{bill_line_id}  json-body={body}")
    resp = client._request(
        "PUT", f"/bill_lines/{bill_line_id}", json=body,
    )
    return _log_outcome("A2 (PUT /bill_lines/{id})", resp.status_code,
                        _safe_body_snippet(resp))


def _run_a3(client, *, bill_line_id: str, body: dict) -> int:
    """PATCH /bill_lines/{id} med samma JSON-body som A2."""
    from app.services.bezala_client import _safe_body_snippet

    print(f"\n→ A3: PATCH /bill_lines/{bill_line_id}  json-body={body}")
    resp = client._request(
        "PATCH", f"/bill_lines/{bill_line_id}", json=body,
    )
    return _log_outcome("A3 (PATCH /bill_lines/{id})", resp.status_code,
                        _safe_body_snippet(resp))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.split("\n\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--method", required=True, choices=("a1", "a2", "a3"),
        help="Vilken kandidatmetod att testa. Kör en i taget.",
    )
    parser.add_argument(
        "--bill-line-id", required=True,
        help="ID på Bezala-bill_line att testa mot. Hämtas från "
             "missing_receipts-listan eller Bezala-UI:t.",
    )
    parser.add_argument(
        "--account-id", type=int, default=67100,
        help="expense_account_id (default 67100 = Matkaliput).",
    )
    parser.add_argument(
        "--cost-center-id", type=int, default=927151,
        help="cost_center_ids[0] (default 927151 = VIS128 Visma HRM Sverige AB).",
    )
    parser.add_argument(
        "--vat-code-id", type=int, default=1355,
        help="vat_code_id (default 1355 = FI 25,5%%).",
    )
    parser.add_argument(
        "--tax-percentage", default="0.255",
        help="tax_percentage som decimal-sträng (default 0.255 = 25,5%%).",
    )
    parser.add_argument(
        "--amount", default="10.00",
        help="taxable-belopp som decimal-sträng med 2 dec (default 10.00).",
    )
    parser.add_argument(
        "--currency", default="EUR",
        help="ISO-valuta för raden (default EUR).",
    )
    parser.add_argument(
        "--description", default="FAS A test (Bezala Bot)",
        help="Beskrivningstext för attachment (bara A1 använder den).",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Visa DEBUG-loggar (inklusive Bezala-request-detaljer).",
    )
    args = parser.parse_args(argv)

    _setup_path()
    _setup_logging(args.verbose)

    if not (os.environ.get("BEZALA_USERNAME") and os.environ.get("BEZALA_PASSWORD")):
        print("FEL: BEZALA_USERNAME och BEZALA_PASSWORD måste vara satta i env.",
              file=sys.stderr)
        return 2

    from app.services.bezala_client import BezalaClient, BezalaError

    try:
        client = BezalaClient()
    except BezalaError as exc:
        print(f"FEL: Kunde inte initialisera BezalaClient: {exc}", file=sys.stderr)
        return 2

    vat_form = _build_vat_line_form_fields(
        expense_account_id=args.account_id,
        cost_center_id=args.cost_center_id,
        vat_code_id=args.vat_code_id,
        tax_percentage=args.tax_percentage,
        taxable=args.amount,
        currency=args.currency,
    )
    vat_json = _build_vat_line_json(
        expense_account_id=args.account_id,
        cost_center_id=args.cost_center_id,
        vat_code_id=args.vat_code_id,
        tax_percentage=args.tax_percentage,
        taxable=args.amount,
        currency=args.currency,
    )

    print("\n" + "*" * 70)
    print("FAS A — match-flödes-metodutforskning")
    print(f"  bill_line_id = {args.bill_line_id}")
    print(f"  method       = {args.method.upper()}")
    print(f"  expense_account_id = {args.account_id}")
    print(f"  cost_center_id     = {args.cost_center_id}")
    print(f"  vat_code_id        = {args.vat_code_id}")
    print(f"  tax_percentage     = {args.tax_percentage}")
    print(f"  taxable (amount)   = {args.amount}")
    print(f"  currency           = {args.currency}")
    print("*" * 70)
    print("VARNING: detta script muterar produktionsdata i Bezala. "
          "Avbryt nu med Ctrl-C om du inte är säker.")
    print("*" * 70)

    try:
        if args.method == "a1":
            rc = _run_a1(
                client, bill_line_id=args.bill_line_id,
                vat_form=vat_form, description=args.description,
            )
        elif args.method == "a2":
            rc = _run_a2(
                client, bill_line_id=args.bill_line_id, body=vat_json,
            )
        else:
            rc = _run_a3(
                client, bill_line_id=args.bill_line_id, body=vat_json,
            )
    except BezalaError as exc:
        print(f"\nFEL — Bezala-anrop kastade exception: {exc}", file=sys.stderr)
        if exc.body:
            print(f"  body={exc.body}", file=sys.stderr)
        return 1
    finally:
        client.close()

    print("\nNästa steg:")
    if rc == 0:
        print(f"  → Metod {args.method.upper()} fungerade. Rapportera "
              "detta i PR-tråden så vi kan gå till FAS B (implementation).")
    else:
        print(f"  → Metod {args.method.upper()} failade. Kör nästa metod "
              "(eller alla 3) och samla response-bodies innan vi gör om strategin.")
    return rc


if __name__ == "__main__":
    sys.exit(main())
