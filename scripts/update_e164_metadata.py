"""Refresh the pinned E.164 calling-code metadata used by privacy scans."""

from __future__ import annotations

import hashlib
import re
import textwrap
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.request import urlopen

SOURCE_COMMIT = "99ade73f8465edd4a71969c8899bc45a854ed100"  # pragma: allowlist secret
SOURCE_SHA256 = (
    "9d93b18cbaffe4c996abe5ca63763385301ec899be5d2821d7ed53828447fc0e"  # pragma: allowlist secret
)
SOURCE_URL = (
    "https://raw.githubusercontent.com/google/libphonenumber/"
    f"{SOURCE_COMMIT}/resources/PhoneNumberMetadata.xml"
)
OUTPUT = Path(__file__).parents[1] / "src" / "charity_donor_outreach" / "_e164.py"
_LENGTH_EXPRESSION = re.compile(r"(?:[0-9]+|\[[0-9]+-[0-9]+\])(?:,(?:[0-9]+|\[[0-9]+-[0-9]+\]))*")


def _possible_lengths(expression: str) -> set[int]:
    if _LENGTH_EXPRESSION.fullmatch(expression) is None:
        raise RuntimeError(f"unexpected possible-length expression: {expression!r}")
    lengths: set[int] = set()
    for token in expression.split(","):
        if token.startswith("["):
            lower, upper = (int(value) for value in token[1:-1].split("-", maxsplit=1))
            lengths.update(range(lower, upper + 1))
        else:
            lengths.add(int(token))
    return lengths


def _parse_metadata(payload: bytes) -> dict[str, frozenset[int]]:
    root = ET.fromstring(payload)
    calling_codes: dict[str, set[int]] = {}
    for territory in root.findall(".//territory"):
        calling_code = territory.attrib.get("countryCode")
        if calling_code is None:
            raise RuntimeError("territory is missing a countryCode")
        lengths = calling_codes.setdefault(calling_code, set())
        for element in territory.findall(".//possibleLengths"):
            expression = element.attrib.get("national")
            if expression:
                lengths.update(_possible_lengths(expression))

    normalized = {code: frozenset(lengths) for code, lengths in calling_codes.items() if lengths}
    if (
        len(normalized) != 215
        or any(
            not code.isascii()
            or not code.isdecimal()
            or not 1 <= len(code) <= 3
            or any(not 4 <= length <= 17 for length in lengths)
            for code, lengths in normalized.items()
        )
        or normalized.get("1") != frozenset({7, 10})
        or normalized.get("44") != frozenset({7, 9, 10})
        or normalized.get("500") != frozenset({5})
    ):
        raise RuntimeError("unexpected E.164 metadata contents")
    return normalized


def _quoted_code_lines(codes: tuple[str, ...]) -> list[str]:
    wrapped = textwrap.wrap(
        " ".join(codes),
        width=82,
        break_long_words=False,
        break_on_hyphens=False,
    )
    return [
        f'        "{line}{" " if index + 1 < len(wrapped) else ""}"'
        for index, line in enumerate(wrapped)
    ]


def _render(metadata: dict[str, frozenset[int]]) -> str:
    assigned_codes = tuple(sorted(metadata, key=int))
    grouped: dict[tuple[int, ...], list[str]] = {}
    for code in assigned_codes:
        grouped.setdefault(tuple(sorted(metadata[code])), []).append(code)

    group_blocks: list[str] = []
    for lengths, codes in sorted(grouped.items()):
        code_lines = _quoted_code_lines(tuple(codes))
        code_lines[-1] += ","
        length_text = " ".join(str(length) for length in lengths)
        group_blocks.append(
            "    (\n" + "\n".join(code_lines) + f'\n        "{length_text}",\n    ),'
        )

    assigned_code_lines = "\n".join(f'        "{code}",' for code in assigned_codes)
    return (
        '"""Pinned E.164 calling-code metadata used by the cross-field privacy guard.\n\n'
        "Source: Google libphonenumber ``PhoneNumberMetadata.xml`` at commit\n"
        f"``{SOURCE_COMMIT}`` (2026-07-30). The metadata is maintained from ITU and\n"
        "national numbering-plan publications.\n"
        f"Source SHA-256: ``{SOURCE_SHA256}``.\n"
        '"""\n\n'
        f'E164_METADATA_COMMIT = "{SOURCE_COMMIT}"  # pragma: allowlist secret\n'
        "E164_METADATA_SHA256 = (\n"
        f'    "{SOURCE_SHA256}"  # pragma: allowlist secret\n'
        ")\n"
        "E164_ASSIGNED_CALLING_CODE_SET = frozenset(\n"
        "    {\n"
        f"{assigned_code_lines}\n"
        "    }\n"
        ")\n\n"
        "_E164_POSSIBLE_LENGTH_GROUPS = (\n" + "\n".join(group_blocks) + "\n)\n\n"
        "E164_POSSIBLE_NATIONAL_LENGTHS: dict[str, frozenset[int]] = {\n"
        "    code: frozenset(int(length) for length in lengths.split())\n"
        "    for codes, lengths in _E164_POSSIBLE_LENGTH_GROUPS\n"
        "    for code in codes.split()\n"
        "}\n"
    )


def main() -> None:
    """Download, verify, parse, and render the pinned numbering metadata."""

    with urlopen(SOURCE_URL, timeout=30) as response:
        payload = response.read()
    digest = hashlib.sha256(payload).hexdigest()
    if digest != SOURCE_SHA256:
        raise RuntimeError(f"source SHA-256 mismatch: {digest}")
    metadata = _parse_metadata(payload)
    OUTPUT.write_text(_render(metadata), encoding="utf-8", newline="\n")
    print(f"wrote {OUTPUT} with {len(metadata)} calling codes (sha256 {digest})")


if __name__ == "__main__":
    main()
