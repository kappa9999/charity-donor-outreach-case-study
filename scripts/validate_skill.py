"""Validate the repository's portable Agent Skill surface."""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Sequence
from pathlib import Path

_NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_LINK_PATTERN = re.compile(r"\[[^\]]+\]\(([^)]+)\)")


def validate(skill_root: Path) -> list[str]:
    errors: list[str] = []
    skill_path = skill_root / "SKILL.md"
    if not skill_path.is_file():
        return [f"missing required file: {skill_path}"]

    text = skill_path.read_text(encoding="utf-8")
    lines = text.splitlines()
    if len(lines) >= 500:
        errors.append("SKILL.md must remain under 500 lines")
    if not lines or lines[0] != "---":
        return [*errors, "SKILL.md must start with YAML frontmatter"]
    try:
        closing = lines.index("---", 1)
    except ValueError:
        return [*errors, "SKILL.md frontmatter is not closed"]

    metadata: dict[str, str] = {}
    for line in lines[1:closing]:
        if ":" not in line:
            errors.append(f"invalid frontmatter line: {line}")
            continue
        key, value = line.split(":", 1)
        metadata[key.strip()] = value.strip()

    if set(metadata) != {"name", "description"}:
        errors.append("frontmatter must contain only name and description")
    name = metadata.get("name", "")
    description = metadata.get("description", "")
    if not _NAME_PATTERN.fullmatch(name) or len(name) > 64:
        errors.append("name must be lowercase hyphen-case and at most 64 characters")
    if name != skill_root.name:
        errors.append("skill name must match its parent directory")
    if not 1 <= len(description) <= 1024:
        errors.append("description must contain between 1 and 1024 characters")
    if "TODO" in text:
        errors.append("SKILL.md contains an unresolved TODO")

    for target in _LINK_PATTERN.findall(text):
        if target.startswith(("http://", "https://", "#")):
            continue
        path = (skill_root / target.split("#", 1)[0]).resolve()
        if not path.exists():
            errors.append(f"broken local reference: {target}")

    required_assets = {
        "campaign-brief.schema.json",
        "donor-record.schema.json",
        "outreach-result.schema.json",
    }
    asset_root = skill_root / "assets"
    existing_assets = (
        {path.name for path in asset_root.iterdir() if path.is_file()}
        if asset_root.is_dir()
        else set()
    )
    missing_assets = sorted(required_assets - existing_assets)
    if missing_assets:
        errors.append(f"missing schema assets: {', '.join(missing_assets)}")
    if not (skill_root / "agents" / "openai.yaml").is_file():
        errors.append("missing agents/openai.yaml")
    return errors


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("skill_root", type=Path)
    args = parser.parse_args(argv)
    errors = validate(args.skill_root.resolve())
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(f"validated: {args.skill_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
