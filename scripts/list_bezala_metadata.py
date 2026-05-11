#!/usr/bin/env python3
"""Lista Bezalas live-metadata för att verifiera konto-IDs och vat_code-IDs.

Kör mot prod-Bezala för att fylla i platshållare i
`app/services/bezala_field_mapper.py`:
  - CATEGORY_TO_ACCOUNT_ID  (None-värden för icke-verifierade kategorier)
  - VAT_PERCENTAGE_BY_CODE  (placeholders för SE/NO och okända FI-koder)

Användning:

  # Lokalt (kräver BEZALA_USERNAME/BEZALA_PASSWORD i env):
  python scripts/list_bezala_metadata.py

  # Eller specificera env-variabler inline:
  BEZALA_USERNAME=mikko@example.com BEZALA_PASSWORD=*** \
      python scripts/list_bezala_metadata.py

  # Filtrera output till bara accounts eller vat_rates:
  python scripts/list_bezala_metadata.py --only accounts
  python scripts/list_bezala_metadata.py --only vat_rates

Output:
  Skriver tabeller med id, name, code (och, för vat_rates, percentage)
  till stdout. Också ett markdown-block redo att klistra in i PR-tråden.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any


def _setup_path() -> None:
    """Säkerställ att app/ är importerbar oavsett CWD."""
    here = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.dirname(here)
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)


def _row_fields(row: dict, keys: tuple[str, ...]) -> str:
    for k in keys:
        v = row.get(k)
        if v not in (None, ""):
            return str(v)
    return ""


def _print_accounts(accounts: list[dict]) -> None:
    print("\n=== ACCOUNTS ===")
    print(f"Antal: {len(accounts)}\n")
    print(f"{'ID':<10} {'NAME':<48} {'CODE':<10} {'default_vat_id'}")
    print("-" * 90)
    for r in accounts:
        rid = _row_fields(r, ("id", "account_id"))
        name = _row_fields(r, ("name", "title", "label"))
        code = _row_fields(r, ("code", "account_code"))
        dvat = _row_fields(r, ("default_vat_id",))
        print(f"{rid:<10} {name[:47]:<48} {code:<10} {dvat}")
    print()
    print("### Markdown för PR-tråden")
    print("```")
    print("| ID | Name | Code | default_vat_id |")
    print("|---|---|---|---|")
    for r in accounts:
        rid = _row_fields(r, ("id", "account_id"))
        name = _row_fields(r, ("name", "title", "label")).replace("|", "\\|")
        code = _row_fields(r, ("code", "account_code"))
        dvat = _row_fields(r, ("default_vat_id",))
        print(f"| {rid} | {name} | {code} | {dvat} |")
    print("```")


def _print_vat_rates(vat_rates: list[dict]) -> None:
    print("\n=== VAT RATES ===")
    print(f"Antal: {len(vat_rates)}\n")
    print(f"{'ID':<10} {'NAME':<48} {'PERCENTAGE':<12} {'COUNTRY'}")
    print("-" * 90)
    for r in vat_rates:
        rid = _row_fields(r, ("id", "vat_code_id", "vat_rate_id"))
        name = _row_fields(r, ("name", "description", "label", "title"))
        pct = _row_fields(r, ("percentage", "tax_percentage", "rate"))
        country = _row_fields(r, ("country", "country_code"))
        print(f"{rid:<10} {name[:47]:<48} {pct:<12} {country}")
    print()
    print("### Markdown för PR-tråden")
    print("```")
    print("| ID | Name | Percentage | Country |")
    print("|---|---|---|---|")
    for r in vat_rates:
        rid = _row_fields(r, ("id", "vat_code_id", "vat_rate_id"))
        name = _row_fields(r, ("name", "description", "label", "title")).replace("|", "\\|")
        pct = _row_fields(r, ("percentage", "tax_percentage", "rate"))
        country = _row_fields(r, ("country", "country_code"))
        print(f"| {rid} | {name} | {pct} | {country} |")
    print("```")


def _print_cost_centers(cost_centers: list[dict]) -> None:
    print("\n=== COST CENTERS ===")
    print(f"Antal: {len(cost_centers)}\n")
    print(f"{'ID':<10} {'NAME':<48} {'CODE':<10} {'default?'}")
    print("-" * 90)
    for r in cost_centers:
        rid = _row_fields(r, ("id", "cost_center_id"))
        name = _row_fields(r, ("name", "title", "label"))
        code = _row_fields(r, ("code", "cost_center_code"))
        default = "YES" if (r.get("default") is True or r.get("is_default") is True) else ""
        print(f"{rid:<10} {name[:47]:<48} {code:<10} {default}")
    print()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--only",
        choices=("accounts", "vat_rates", "cost_centers", "all"),
        default="all",
        help="Filtrera output till en kategori (default: all)",
    )
    args = parser.parse_args(argv)

    _setup_path()

    if not (os.environ.get("BEZALA_USERNAME") and os.environ.get("BEZALA_PASSWORD")):
        print("FEL: BEZALA_USERNAME och BEZALA_PASSWORD måste vara satta i env.",
              file=sys.stderr)
        return 2

    # Lazy import efter path-setup
    from app.services.bezala_client import BezalaClient, BezalaError

    try:
        client = BezalaClient()
    except BezalaError as exc:
        print(f"FEL: Kunde inte initialisera BezalaClient: {exc}", file=sys.stderr)
        return 1

    try:
        if args.only in ("accounts", "all"):
            try:
                _print_accounts(client.list_accounts())
            except BezalaError as exc:
                print(f"FEL list_accounts: {exc}", file=sys.stderr)
        if args.only in ("vat_rates", "all"):
            try:
                _print_vat_rates(client.list_vat_rates())
            except BezalaError as exc:
                print(f"FEL list_vat_rates: {exc}", file=sys.stderr)
        if args.only in ("cost_centers", "all"):
            try:
                _print_cost_centers(client.list_cost_centers())
            except BezalaError as exc:
                print(f"FEL list_cost_centers: {exc}", file=sys.stderr)
    finally:
        client.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
