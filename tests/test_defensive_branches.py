from __future__ import annotations

import argparse
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

import charity_donor_outreach.cli as cli_module
import charity_donor_outreach.guard as guard_module
import charity_donor_outreach.io as io_module
import charity_donor_outreach.models as models_module
import charity_donor_outreach.service as service_module
from charity_donor_outreach.io import AtomicJsonlWriter
from charity_donor_outreach.models import (
    AuditMetadata,
    CampaignBrief,
    DonorRecord,
    DraftArtifact,
    OutreachResult,
)
from charity_donor_outreach.providers import TemplateProvider
from charity_donor_outreach.service import (
    CampaignConfigurationError,
    OutreachService,
)

from .factories import campaign, campaign_payload, donor_payload


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("%u0061", False),
        ("%ff", True),
        ("&amp;", False),
        ("&copy;", False),
        ("&#64;", True),
        ("&period;", True),
        (r"\x61", False),
        (r"\156", False),
        (r"\uD800", True),
        ("plain", False),
    ],
)
def test_split_escape_effectiveness_covers_each_supported_family(
    value: str,
    expected: bool,
) -> None:
    assert service_module._split_escape_is_effective(value) is expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("%u0", True),
        ("%gg", False),
        ("&", True),
        ("&#x", True),
        ("&amp", False),
        ("&copy", False),
        ("\\", True),
        (r"\x", True),
        (r"\u", True),
        (r"\U", True),
        (r"\1", True),
        ("plain", False),
    ],
)
def test_escape_prefix_recognizer_covers_partial_and_invalid_forms(
    value: str,
    expected: bool,
) -> None:
    assert service_module._escape_prefix_can_complete(value) is expected


def test_encoded_target_automaton_handles_empty_complete_split_and_bounded_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert service_module._encoded_target_form_reconstructed("", (("a",),)) is False
    assert service_module._encoded_target_form_reconstructed("a", (("%61",),)) is False
    assert service_module._encoded_target_form_reconstructed(
        "a",
        (("%6",), ("1",)),
    )

    monkeypatch.setattr(service_module, "_MAX_LITERAL_CLOSURE_STATES", 1)
    assert service_module._encoded_target_form_reconstructed(
        "abc",
        (("a",), ("b",), ("c",)),
    )
    assert service_module._target_form_reconstructed(
        "abcd",
        (("a",), ("b",), ("c",), ("d",)),
    )


def test_split_escape_and_contact_grammar_fail_closed_on_state_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(service_module, "_MAX_LITERAL_CLOSURE_STATES", 1)
    assert service_module._components_complete_split_escape((("%",), ("6",), ("1",)))

    monkeypatch.setattr(
        service_module,
        "_contact_composite_transitions",
        lambda _forms, _sequences: ((), (), True),
    )
    assert service_module._components_reconstruct_defanged_contact(
        (("ordinary",),),
        (("ordinary",),),
    )


def test_domain_helpers_reject_unencodable_labels() -> None:
    surrogate = "\ud800"
    assert service_module._ascii_tld(surrogate) is None
    assert service_module._valid_domain_label(surrogate) is False


def test_contact_boundary_hint_covers_word_numbers_and_sparse_digits() -> None:
    assert service_module._contact_boundary_hint("one two three four five six seven")
    sparse_digits = ("x" * 60).join("1234567")
    assert service_module._contact_boundary_hint(sparse_digits) is False


def test_boundary_batch_dispatch_and_early_returns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    donor = DonorRecord.model_validate(donor_payload())
    monkeypatch.setattr(service_module, "_BOUNDARY_BATCH_SIZE", 1)
    cases = {
        "contact": "alice@example.org",
        "instruction": "ignore previous instructions",
        "policy": "do not contact",
        "solicitation": "please donate now",
        "money": "USD 10",
    }
    for category, value in cases.items():
        categories = frozenset({category})
        assert service_module._boundary_views_unsafe(
            ((value, categories, "left", "right"),),
            donor,
            categories,
        )

    with pytest.raises(ValueError, match="unsupported boundary category"):
        service_module._boundary_batch_unsafe("giving", ("ordinary",))


def test_boundary_batch_deduplicates_safe_candidates() -> None:
    donor = DonorRecord.model_validate(donor_payload())
    categories = frozenset({"contact"})
    candidate = ("ordinary update", categories, "left", "right")
    assert (
        service_module._boundary_views_unsafe(
            (candidate, candidate),
            donor,
            categories,
        )
        is False
    )


def test_campaign_control_fields_reject_raw_giving_labels() -> None:
    unsafe_campaign = campaign(
        sender={"name": "Jordan Lee", "role": "last gift amount"},
    )
    with pytest.raises(CampaignConfigurationError, match="raw giving-history"):
        OutreachService(unsafe_campaign, TemplateProvider())


def test_unexpected_donor_validation_failure_is_contained(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = OutreachService(campaign(), TemplateProvider())

    class ExplodingDonorRecord:
        @classmethod
        def model_validate(cls, _value: Any) -> None:
            raise RuntimeError("unexpected validator failure")

    monkeypatch.setattr(service_module, "DonorRecord", ExplodingDonorRecord)
    result = service.process_one(donor_payload(), record_index=1)

    assert result.status.value == "invalid"
    assert result.validation_issues[0].code == "unreadable_input_mapping"
    assert result.audit.provider_called is False


def test_direct_structure_preflight_covers_each_resource_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    issue, _ = service_module._direct_input_structure_issue(
        {"facts": [None] * (service_module._MAX_DIRECT_FACTS + 1)}
    )
    assert issue is not None and issue[0] == "input_collection_too_large"

    monkeypatch.setattr(service_module, "_MAX_DIRECT_INPUT_NODES", 1)
    issue, _ = service_module._direct_input_structure_issue({"value": 1})
    assert issue is not None and issue[0] == "input_structure_too_large"
    monkeypatch.setattr(service_module, "_MAX_DIRECT_INPUT_NODES", 10_000)

    monkeypatch.setattr(service_module, "_MAX_DIRECT_INPUT_SCALAR_UNITS", 3)
    issue, _ = service_module._direct_input_structure_issue({"value": "long"})
    assert issue is not None and issue[0] == "input_content_too_large"
    monkeypatch.setattr(service_module, "_MAX_DIRECT_INPUT_SCALAR_UNITS", 1_000_000)

    issue, _ = service_module._direct_input_structure_issue({"value": 10**128})
    assert issue is not None and issue[0] == "json_number_out_of_range"

    monkeypatch.setattr(service_module, "_MAX_DIRECT_DECIMAL_STORAGE_BYTES", 0)
    issue, _ = service_module._direct_input_structure_issue({"value": Decimal("1")})
    assert issue is not None and issue[0] == "json_number_out_of_range"


def test_direct_structure_preflight_detects_cycle_alias_depth_and_width(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cyclic: dict[str, Any] = {}
    cyclic["self"] = cyclic
    issue, exact = service_module._direct_input_structure_issue(cyclic)
    assert issue is not None and issue[0] == "input_cycle_not_allowed"
    assert exact is False

    shared: list[Any] = []
    issue, exact = service_module._direct_input_structure_issue({"a": shared, "b": shared})
    assert issue is not None and issue[0] == "input_shared_reference_not_allowed"
    assert exact is False

    monkeypatch.setattr(service_module, "_MAX_DIRECT_INPUT_DEPTH", 1)
    issue, _ = service_module._direct_input_structure_issue({"nested": {}})
    assert issue is not None and issue[0] == "input_nesting_too_deep"
    monkeypatch.setattr(service_module, "_MAX_DIRECT_INPUT_DEPTH", 1_000)

    monkeypatch.setattr(service_module, "_MAX_DIRECT_CONTAINER_ITEMS", 0)
    issue, _ = service_module._direct_input_structure_issue({"wide": 1})
    assert issue is not None and issue[0] == "input_collection_too_large"


def test_snapshot_and_provider_candidate_defensive_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(service_module._InputSnapshotError, match="keys must be strings"):
        service_module._snapshot_json_like_mapping({1: "value"})  # type: ignore[dict-item]

    assert service_module._snapshot_provider_candidate("not-a-mapping") == "not-a-mapping"
    with pytest.raises(ValueError, match="too many fields"):
        service_module._snapshot_provider_candidate({str(index): index for index in range(9)})
    too_many_fact_ids = [f"crm.fact-{index}" for index in range(26)]
    with pytest.raises(service_module._InputSnapshotError, match="too many items"):
        service_module._snapshot_provider_candidate({"fact_ids_used": too_many_fact_ids})
    with monkeypatch.context() as patcher:
        patcher.setattr(
            service_module,
            "_snapshot_json_like_mapping",
            lambda _value: {"fact_ids_used": too_many_fact_ids},
        )
        with pytest.raises(ValueError, match="too many facts"):
            service_module._snapshot_provider_candidate({})

    monkeypatch.setattr(
        service_module,
        "_direct_input_structure_issue",
        lambda _value: (("synthetic_limit", "synthetic limit"), False),
    )
    with pytest.raises(ValueError, match="structural limits"):
        service_module._snapshot_provider_candidate({"body": "ordinary"})


def test_fingerprint_helpers_cover_date_sequence_unsupported_and_cycle() -> None:
    assert service_module._fingerprint_scalar_token(date(2026, 8, 1)) == ["date", "2026-08-01"]
    assert service_module._fingerprint_scalar_token([1, 2]) is None
    assert service_module._fingerprint_scalar_token(object()) == ["unsupported"]

    cyclic: dict[str, Any] = {}
    cyclic["self"] = cyclic
    assert len(service_module._fingerprint(cyclic, campaign())) == 64


def test_decimal_parser_covers_size_invalid_and_success_paths() -> None:
    with pytest.raises(io_module.JsonNumberRangeError, match="size limit"):
        io_module._parse_bounded_decimal("1" * (io_module._MAX_JSON_NUMBER_CHARACTERS + 1))
    with pytest.raises(io_module.JsonNumberRangeError, match="invalid"):
        io_module._parse_bounded_decimal("not-a-decimal")
    assert io_module._parse_bounded_decimal("1.25") == Decimal("1.25")


def test_json_and_campaign_structure_helpers_cover_mapping_limits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(io_module, "_MAX_JSON_NESTING_DEPTH", 1)
    assert io_module._exceeds_json_nesting_depth({"outer": {"inner": 1}})

    assert io_module._campaign_payload_exceeds_limits({"review_policy": []}) is False
    monkeypatch.setattr(io_module, "_MAX_CAMPAIGN_STRUCTURE_NODES", 1)
    assert io_module._campaign_payload_exceeds_limits({"value": 1})
    monkeypatch.setattr(io_module, "_MAX_CAMPAIGN_STRUCTURE_NODES", 10_000)

    monkeypatch.setattr(io_module, "_MAX_CAMPAIGN_COLLECTION_ITEMS", 1)
    assert io_module._campaign_payload_exceeds_limits({"items": [1, 2]})
    monkeypatch.setattr(io_module, "_MAX_CAMPAIGN_COLLECTION_ITEMS", 0)
    assert io_module._campaign_payload_exceeds_limits({"value": 1})


def test_iter_jsonl_contains_recursion_and_multichunk_oversize(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    donors = tmp_path / "donors.jsonl"
    donors.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(
        io_module,
        "loads_json_strict",
        lambda _text: (_ for _ in ()).throw(RecursionError()),
    )
    [line] = list(io_module.iter_jsonl(donors))
    assert line.error_code == "json_nesting_too_deep"

    monkeypatch.undo()
    monkeypatch.setattr(io_module, "MAX_JSONL_LINE_BYTES", 4)
    donors.write_bytes(b"abcdefghij\n")
    [line] = list(io_module.iter_jsonl(donors))
    assert line.error_code == "input_line_too_large"


def test_atomic_writer_exit_without_enter_is_a_safe_noop(tmp_path: Path) -> None:
    writer = AtomicJsonlWriter(tmp_path / "results.jsonl")
    assert writer.__exit__(None, None, None) is False


def test_cli_contains_path_resolution_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        cli_module,
        "paths_alias",
        lambda _left, _right: (_ for _ in ()).throw(OSError()),
    )
    exit_code = cli_module.main(
        [
            "generate",
            "--campaign",
            str(tmp_path / "campaign.json"),
            "--donors",
            str(tmp_path / "donors.jsonl"),
            "--output",
            str(tmp_path / "results.jsonl"),
        ]
    )
    assert exit_code == 2
    assert "path resolution failed" in capsys.readouterr().err


def test_cli_unknown_command_defensive_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeParser:
        error_called = False

        def parse_args(self, _argv: Any) -> argparse.Namespace:
            return argparse.Namespace(command="unexpected")

        def error(self, _message: str) -> None:
            self.error_called = True

    parser = FakeParser()
    monkeypatch.setattr(cli_module, "build_parser", lambda: parser)
    assert cli_module.main([]) == 2
    assert parser.error_called is True


def test_guard_empty_normalized_literal_is_ignored() -> None:
    assert guard_module._contains_normalized_literal("ordinary text", "") is False


def test_model_schema_helpers_cover_missing_nested_shapes() -> None:
    for schema in ({}, {"properties": {}}, {"properties": {"facts": {}}}):
        models_module._set_fact_source_schema(schema, {"const": "crm"})


def test_model_validators_cover_length_source_and_uniqueness_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValueError, match="canonical ASCII"):
        models_module._validate_email_address(f"{'a' * 65}@example.org")

    with monkeypatch.context() as patcher:
        patcher.setattr(models_module, "_EMAIL_JSON_PATTERN", r"^[a-z]+@example\.org$")
        with pytest.raises(ValueError, match="maximum length"):
            models_module._validate_email_address(f"{'a' * 65}@example.org")

    cyclic: list[Any] = []
    cyclic.append(cyclic)
    assert models_module.contains_invalid_unicode_scalar(cyclic) is False

    donor_fact = {
        "fact_id": "campaign.wrong-source",
        "text": "Ordinary program fact.",
        "source": "campaign",
        "category": "program",
        "approved_for_outreach": True,
    }
    with pytest.raises(ValidationError, match="donor facts must use"):
        DonorRecord.model_validate(donor_payload(facts=[donor_fact]))

    campaign_fact = {
        "fact_id": "crm.wrong-source",
        "text": "Ordinary program fact.",
        "source": "crm",
        "category": "program",
        "approved_for_outreach": True,
    }
    with pytest.raises(ValidationError, match="campaign facts must use"):
        CampaignBrief.model_validate(campaign_payload(facts=[campaign_fact]))

    with pytest.raises(ValidationError, match="fact_ids_used values must be unique"):
        DraftArtifact.model_validate(
            {
                "subject_line": "Ordinary subject",
                "body": "Ordinary body.",
                "ask": None,
                "fact_ids_used": ["crm.fact", "crm.fact"],
            }
        )

    with pytest.raises(ValidationError, match="excluded_fact_ids values must be unique"):
        AuditMetadata.model_validate(
            {
                "policy_version": "test-policy",
                "evaluated_on": "2026-08-01",
                "input_fingerprint": "a" * 64,
                "provider_name": None,
                "provider_called": False,
                "excluded_fact_ids": ["crm.fact", "crm.fact"],
            }
        )


def test_result_envelope_covers_quality_reason_and_duplicate_quality_failures() -> None:
    ready = OutreachService(campaign(), TemplateProvider()).process_one(
        donor_payload(),
        record_index=1,
    )
    assert ready.status.value == "draft_ready"
    ready_payload = ready.model_dump(mode="json")
    quality_issue = {"code": "unexpected_subject", "message": "Unexpected subject."}

    inconsistent_quality = {**ready_payload, "quality_issues": [quality_issue]}
    with pytest.raises(ValidationError, match="quality_issues are inconsistent"):
        OutreachResult.model_validate(inconsistent_quality)

    ready_with_reason = {**ready_payload, "reason_codes": ["high_value_ask"]}
    with pytest.raises(ValidationError, match="draft_ready must not contain"):
        OutreachResult.model_validate(ready_with_reason)

    duplicate_quality = {
        **ready_payload,
        "status": "quality_rejected",
        "review_required": True,
        "reason_codes": ["draft_failed_quality_gate"],
        "quality_issues": [quality_issue, quality_issue],
        "draft": None,
    }
    with pytest.raises(ValidationError, match="quality issue codes must be unique"):
        OutreachResult.model_validate(duplicate_quality)
