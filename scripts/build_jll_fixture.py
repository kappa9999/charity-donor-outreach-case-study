"""Reproduce the operational fixture derived from JLL's supplied donor table."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from collections.abc import Sequence
from itertools import pairwise
from pathlib import Path
from typing import Final

ROOT: Final = Path(__file__).resolve().parents[1]
FIXTURE_DIR: Final = ROOT / "examples" / "jll-supplied"
SOURCE_PATH: Final = FIXTURE_DIR / "source-donors.csv"
DONORS_PATH: Final = FIXTURE_DIR / "donors.jsonl"
MANIFEST_PATH: Final = FIXTURE_DIR / "fixture-manifest.json"

ATTACHMENT_FILENAME: Final = "charity-donor-outreach.zip"
ATTACHMENT_BYTES: Final = 5_495
ATTACHMENT_SHA256: Final = (
    "08833833da105c65242643ed07049c7de"  # pragma: allowlist secret
    "bd931f9bbfd348690831c49f8d64f5b"  # pragma: allowlist secret
)
SOURCE_SHA256: Final = (
    "40ccc01cb64abd457ce268f63c20306b"  # pragma: allowlist secret
    "452173f27f3864661871c09d2ed28cff"  # pragma: allowlist secret
)
EXPECTED_COLUMNS: Final = (
    "donor_id",
    "donor_name",
    "first_name",
    "last_name",
    "tier",
    "region",
    "gifts",
    "largest_gift",
    "lifetime_total",
    "last_gift_year",
    "volunteer",
)
TIER_TO_SEGMENT: Final = {
    "Platinum": "principal",
    "Gold": "major",
    "Silver": "mid_value",
    "Lapsed": "lapsed",
    "Bronze": "general",
}
REGIONS: Final = {"Northeast", "Southeast", "Midwest", "West", "International"}
GIFT_ENTRY: Final = re.compile(r"([0-9]{4}): \$([0-9][0-9,]*)")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _require(row: dict[str, str], field: str, row_number: int) -> str:
    value = row.get(field)
    if value is None or value == "":
        raise ValueError(f"source row {row_number} has no {field}")
    return value


def _parse_money(value: str, field: str, row_number: int) -> int:
    if re.fullmatch(r"[1-9][0-9]*", value) is None:
        raise ValueError(f"source row {row_number} has invalid {field}")
    return int(value)


def _parse_gifts(value: str, row_number: int) -> list[tuple[int, int]]:
    matches = [
        (int(year), int(amount.replace(",", ""))) for year, amount in GIFT_ENTRY.findall(value)
    ]
    canonical = ", ".join(f"{year}: ${amount:,}" for year, amount in matches)
    if not matches or canonical != value:
        raise ValueError(f"source row {row_number} has invalid gifts")
    if any(current_year >= next_year for (current_year, _), (next_year, _) in pairwise(matches)):
        raise ValueError(f"source row {row_number} has unordered gift years")
    return matches


def _load_and_validate_source() -> list[dict[str, str]]:
    if _sha256(SOURCE_PATH.read_bytes()) != SOURCE_SHA256:
        raise ValueError(
            "source fixture hash no longer matches the verified attachment transcription"
        )
    with SOURCE_PATH.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != EXPECTED_COLUMNS:
            raise ValueError("source fixture columns changed")
        rows = [{key: value or "" for key, value in row.items()} for row in reader]

    if len(rows) != 50:
        raise ValueError("source fixture must contain the 50 supplied rows")

    for index, row in enumerate(rows, start=1):
        expected_id = f"ORIG-{index:03d}"
        if _require(row, "donor_id", index) != expected_id:
            raise ValueError(f"source row {index} has an unexpected donor_id")
        first_name = _require(row, "first_name", index)
        last_name = _require(row, "last_name", index)
        if _require(row, "donor_name", index) != f"{first_name} {last_name}":
            raise ValueError(f"source row {index} has inconsistent name fields")
        tier = _require(row, "tier", index)
        if tier not in TIER_TO_SEGMENT:
            raise ValueError(f"source row {index} has an unknown tier")
        if _require(row, "region", index) not in REGIONS:
            raise ValueError(f"source row {index} has an unknown region")
        if _require(row, "volunteer", index) not in {"True", "False"}:
            raise ValueError(f"source row {index} has an invalid volunteer flag")

        gifts = _parse_gifts(_require(row, "gifts", index), index)
        largest_gift = _parse_money(_require(row, "largest_gift", index), "largest_gift", index)
        lifetime_total = _parse_money(
            _require(row, "lifetime_total", index), "lifetime_total", index
        )
        last_gift_year = int(_require(row, "last_gift_year", index))
        if max(amount for _, amount in gifts) != largest_gift:
            raise ValueError(f"source row {index} has inconsistent largest_gift")
        if sum(amount for _, amount in gifts) != lifetime_total:
            raise ValueError(f"source row {index} has inconsistent lifetime_total")
        if gifts[-1][0] != last_gift_year:
            raise ValueError(f"source row {index} has inconsistent last_gift_year")
    return rows


def _operational_record(row: dict[str, str], row_number: int) -> dict[str, object]:
    gifts = _parse_gifts(_require(row, "gifts", row_number), row_number)
    tier = _require(row, "tier", row_number)
    return {
        "donor_id": _require(row, "donor_id", row_number),
        "first_name": _require(row, "first_name", row_number),
        "last_name": _require(row, "last_name", row_number),
        "title": None,
        "preferred_channel": "email",
        "channel_consent": "granted",
        "do_not_contact": False,
        "email": f"jll-sample-{row_number:03d}@example.org",
        "postal_address": None,
        "segment": TIER_TO_SEGMENT[tier],
        "giving": {
            "currency": "USD",
            "last_gift_amount": f"{gifts[-1][1]}.00",
            "largest_gift_amount": f"{int(row['largest_gift'])}.00",
            "lifetime_value": f"{int(row['lifetime_total'])}.00",
            "last_gift_date": f"{gifts[-1][0]}-12-31",
        },
        "last_contact_date": None,
        "facts": [],
    }


def _render() -> tuple[str, str]:
    rows = _load_and_validate_source()
    donor_lines = [
        json.dumps(_operational_record(row, index), ensure_ascii=False, separators=(",", ":"))
        for index, row in enumerate(rows, start=1)
    ]
    donors_text = "\n".join(donor_lines) + "\n"
    manifest = {
        "attachment": {
            "filename": ATTACHMENT_FILENAME,
            "bytes": ATTACHMENT_BYTES,
            "sha256": ATTACHMENT_SHA256,
            "embedded_table_rows": 50,
        },
        "source_fixture": {
            "path": "examples/jll-supplied/source-donors.csv",
            "sha256": SOURCE_SHA256,
            "rows": len(rows),
            "columns": list(EXPECTED_COLUMNS),
            "normalization_columns_not_present_in_attachment": [
                "donor_id",
                "first_name",
                "last_name",
            ],
        },
        "operational_fixture": {
            "path": "examples/jll-supplied/donors.jsonl",
            "sha256": _sha256(donors_text.encode("utf-8")),
            "records": len(donor_lines),
        },
        "field_lineage": {
            "preserved_from_attachment": [
                "donor_name represented as first_name plus last_name",
                "tier represented as segment through the documented mapping",
                "gift history represented through validated giving aggregates",
                "largest_gift_amount",
                "lifetime_value",
            ],
            "derived_fields": [
                "stable ORIG row identifier",
                "first_name and last_name split from donor_name",
                "last_gift_amount from the final supplied gift entry",
                "segment from the supplied tier",
                "December 31 placeholder date from the supplied last-gift year",
            ],
            "synthetic_test_controls": [
                "preferred_channel=email",
                "channel_consent=granted",
                "do_not_contact=false",
                "reserved example.org email",
                "title=null",
                "last_contact_date=null",
                "facts=[]",
            ],
            "source_only_fields": [
                "region",
                "volunteer",
                "gift years",
            ],
        },
        "safety_note": (
            "The source CSV is a lossless normalized transcription: donor_id and split-name "
            "columns are bookkeeping fields not present in the attachment. The supplied table "
            "does not contain channel consent, suppression, contact-path, "
            "campaign-control, or approved-claim fields. The operational fixture adds visibly "
            "labelled test controls only; production onboarding must obtain those values from "
            "authoritative systems and must never infer them."
        ),
    }
    manifest_text = json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"
    return donors_text, manifest_text


def _write(donors_text: str, manifest_text: str) -> None:
    DONORS_PATH.write_text(donors_text, encoding="utf-8", newline="\n")
    MANIFEST_PATH.write_text(manifest_text, encoding="utf-8", newline="\n")


def _check(donors_text: str, manifest_text: str) -> None:
    if not DONORS_PATH.exists() or DONORS_PATH.read_text(encoding="utf-8") != donors_text:
        raise ValueError("operational donor fixture is missing or stale; run with --write")
    if not MANIFEST_PATH.exists() or MANIFEST_PATH.read_text(encoding="utf-8") != manifest_text:
        raise ValueError("fixture manifest is missing or stale; run with --write")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--write", action="store_true", help="Regenerate committed derived files.")
    mode.add_argument("--check", action="store_true", help="Check committed files without writing.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        donors_text, manifest_text = _render()
        if args.write:
            _write(donors_text, manifest_text)
        else:
            _check(donors_text, manifest_text)
    except (OSError, UnicodeError, csv.Error, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "mode": "write" if args.write else "check",
                "records": 50,
                "source": str(SOURCE_PATH.relative_to(ROOT)).replace("\\", "/"),
                "status": "ok",
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
