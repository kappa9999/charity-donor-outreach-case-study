"""Command-line entry point."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

from pydantic import ValidationError

from .io import AtomicJsonlWriter, StrictJsonError, iter_jsonl, load_campaign, paths_alias
from .models import ResultStatus
from .providers import TemplateProvider
from .schemas import write_schemas
from .service import (
    CampaignConfigurationError,
    OutreachService,
    ProviderConfigurationError,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="charity-donor-outreach",
        description="Generate policy-checked donor outreach drafts.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    generate = subparsers.add_parser(
        "generate",
        help="Process a campaign and donor JSONL file.",
    )
    generate.add_argument("--campaign", type=Path, required=True)
    generate.add_argument("--donors", type=Path, required=True)
    generate.add_argument("--output", type=Path, required=True)

    schema = subparsers.add_parser(
        "export-schemas",
        help="Export the current executable JSON Schemas.",
    )
    schema.add_argument("--output-dir", type=Path, required=True)
    return parser


def _generate(args: argparse.Namespace) -> int:
    try:
        if paths_alias(args.output, args.campaign) or paths_alias(args.output, args.donors):
            print("output path must differ from campaign and donor input paths", file=sys.stderr)
            return 2
    except OSError:
        print("input or output path resolution failed", file=sys.stderr)
        return 2

    try:
        campaign = load_campaign(args.campaign)
        service = OutreachService(campaign, TemplateProvider())
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        RecursionError,
        StrictJsonError,
        ValidationError,
        CampaignConfigurationError,
        ProviderConfigurationError,
    ):
        print("campaign configuration is invalid; no donor records were processed", file=sys.stderr)
        return 2

    counts: Counter[str] = Counter()
    records = 0
    failing_statuses = {
        ResultStatus.INVALID,
        ResultStatus.PROVIDER_ERROR,
        ResultStatus.QUALITY_REJECTED,
    }
    has_failure = False
    try:
        with AtomicJsonlWriter(args.output) as writer:
            for line in iter_jsonl(args.donors):
                if line.value is None:
                    assert line.error_code is not None
                    assert line.error_message is not None
                    result = service.invalid_input_result(
                        record_index=line.line_number,
                        code=line.error_code,
                        message=line.error_message,
                        input_digest=line.input_digest,
                    )
                else:
                    result = service.process_one(line.value, line.line_number)
                writer.write(result)
                records += 1
                counts[result.status.value] += 1
                has_failure = has_failure or result.status in failing_statuses
    except (OSError, UnicodeError):
        print("input or output file operation failed", file=sys.stderr)
        return 2

    print(json.dumps({"records": records, "statuses": dict(sorted(counts.items()))}))
    return 2 if has_failure else 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "generate":
        return _generate(args)
    if args.command == "export-schemas":
        try:
            write_schemas(args.output_dir)
        except OSError:
            print("schema output failed", file=sys.stderr)
            return 2
        return 0
    parser.error("unknown command")
    return 2
