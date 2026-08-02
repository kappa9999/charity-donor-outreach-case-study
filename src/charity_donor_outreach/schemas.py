"""Export the executable Pydantic contracts as JSON Schema."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import CampaignBrief, DonorRecord, OutreachResult


def _schema_for(
    model: type[CampaignBrief] | type[DonorRecord] | type[OutreachResult],
) -> dict[str, Any]:
    document = model.model_json_schema()
    document["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    document["$comment"] = (
        "Generated from the executable Pydantic contract. Runtime validation remains "
        "authoritative for ordered cross-field comparisons and policy boundaries that "
        "standard JSON Schema cannot express."
    )
    return document


def schema_documents() -> dict[str, dict[str, Any]]:
    return {
        "campaign-brief.schema.json": _schema_for(CampaignBrief),
        "donor-record.schema.json": _schema_for(DonorRecord),
        "outreach-result.schema.json": _schema_for(OutreachResult),
    }


def write_schemas(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for filename, document in schema_documents().items():
        with (output_dir / filename).open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(document, indent=2, sort_keys=True) + "\n")
