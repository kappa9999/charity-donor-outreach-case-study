"""Refresh the deterministic IANA root-zone TLD snapshot used by privacy scans."""

from __future__ import annotations

import hashlib
import re
import textwrap
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import urlopen

SOURCE_URL = "https://data.iana.org/TLD/tlds-alpha-by-domain.txt"
OUTPUT = Path(__file__).parents[1] / "src" / "charity_donor_outreach" / "_iana_tlds.py"
MAX_SOURCE_BYTES = 256 * 1024
SOURCE_HOST = "data.iana.org"
VERSION_PATTERN = re.compile(r"^# Version (?P<version>[0-9]{10}), Last Updated (?P<updated>.+)$")


def _download_source() -> bytes:
    """Download the fixed HTTPS source without accepting a host or scheme redirect."""

    configured = urlsplit(SOURCE_URL)
    if configured.scheme != "https" or configured.hostname != SOURCE_HOST:
        raise RuntimeError("unexpected IANA source URL")
    # SOURCE_URL is a module constant checked above; the final URL is checked before use.
    with urlopen(SOURCE_URL, timeout=30) as response:  # nosec B310
        final = urlsplit(response.geturl())
        if (
            getattr(response, "status", None) != 200
            or final.scheme != "https"
            or final.hostname != SOURCE_HOST
            or final.username is not None
            or final.password is not None
        ):
            raise RuntimeError("unexpected IANA source response")
        payload = bytes(response.read(MAX_SOURCE_BYTES + 1))
    if len(payload) > MAX_SOURCE_BYTES:
        raise RuntimeError("IANA source exceeds the download limit")
    return payload


def main() -> None:
    """Download, validate, and render the official ASCII root-zone labels."""

    payload = _download_source()
    text = payload.decode("ascii")
    lines = text.splitlines()
    metadata = VERSION_PATTERN.fullmatch(lines[0])
    if metadata is None:
        raise RuntimeError("unexpected IANA TLD metadata header")
    labels = tuple(line.strip().lower() for line in lines[1:] if line.strip())
    if (
        len(labels) < 1_000
        or labels != tuple(sorted(set(labels)))
        or any(
            re.fullmatch(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)", label) is None
            for label in labels
        )
    ):
        raise RuntimeError("unexpected IANA TLD snapshot contents")

    wrapped = [
        "    " + line
        for line in textwrap.wrap(
            " ".join(labels),
            width=92,
            break_long_words=False,
            break_on_hyphens=False,
        )
    ]
    digest = hashlib.sha256(payload).hexdigest()
    rendered = (
        '"""Pinned IANA root-zone TLDs for bounded contact-detail detection.\n\n'
        f"Source: {SOURCE_URL}\n"
        f"IANA version: {metadata['version']}\n"
        f"Last updated: {metadata['updated']}\n"
        f"SHA-256: {digest}\n"
        '"""\n\n'
        f'IANA_ROOT_ZONE_VERSION = "{metadata["version"]}"\n'
        "IANA_ROOT_ZONE_SHA256 = (  # pragma: allowlist secret\n"
        f'    "{digest}"  # pragma: allowlist secret\n'
        ")\n"
        "IANA_ROOT_ZONE_TLDS = tuple(\n"
        '    """\n'
        + "\n".join(wrapped)
        + '\n    """.split()  # noqa: SIM905 - compact, auditable standards snapshot\n'
        ")\n"
        "IANA_ROOT_ZONE_TLD_SET = frozenset(IANA_ROOT_ZONE_TLDS)\n"
    )
    OUTPUT.write_text(rendered, encoding="utf-8", newline="\n")
    print(f"wrote {OUTPUT} with {len(labels)} TLDs (IANA {metadata['version']}, sha256 {digest})")


if __name__ == "__main__":
    main()
