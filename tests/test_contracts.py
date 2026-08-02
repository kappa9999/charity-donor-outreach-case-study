from __future__ import annotations

import json
import re
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError as JsonSchemaValidationError
from pydantic import ValidationError

from charity_donor_outreach._e164 import (
    E164_ASSIGNED_CALLING_CODE_SET,
    E164_METADATA_COMMIT,
    E164_METADATA_SHA256,
    E164_POSSIBLE_NATIONAL_LENGTHS,
)
from charity_donor_outreach._iana_tlds import (
    IANA_ROOT_ZONE_SHA256,
    IANA_ROOT_ZONE_TLD_SET,
    IANA_ROOT_ZONE_TLDS,
    IANA_ROOT_ZONE_VERSION,
)
from charity_donor_outreach.models import (
    CampaignBrief,
    DonorRecord,
    DraftCandidate,
    DraftRequest,
    OutreachResult,
    QualityCode,
    ReasonCode,
)
from charity_donor_outreach.schemas import schema_documents, write_schemas

from .factories import campaign_payload, donor_payload

ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = ROOT / "charity-donor-outreach"
UNSAFE_CTA_URLS = (
    "http://donate.example.org/path",
    "HTTPS://donate.example.org/path",
    "https://u:p@donate.example.org/path",
    "https://donate.example.org/path?api_key=secret-value",
    "https://donate.example.org/path#access-token",
    "https://donate.example.org/a b",
    "https://donate.example.org/%0aheader",
    "https://donate.example.org/%ZZ",
    "https://donate.example.org\\evil",
    "https://donate.example.org:443/path",
    "https://donate.example.org:/path",
    "https://donate.example.org:bad/path",
    "https://donate.example.org:99999/path",
    "https://.example.org/path",
    "https://donate..example.org/path",
    "https://localhost/path",
    "https://127.0.0.1/path",
    "https://donate.example.org/caf\u00e9",
    f"https://{'a' * 64}.example.org/path",
    f"https://{'a.' * 126}example.org/path",
    "relative/path",
)


def test_pinned_iana_root_zone_snapshot_is_complete_and_self_consistent() -> None:
    assert IANA_ROOT_ZONE_VERSION == "2026080100"
    assert IANA_ROOT_ZONE_SHA256 == (
        "1671b6044a0a918d39a986eb7d4b8686"  # pragma: allowlist secret
        "55ed832af17dbb85217a1b73297ccf85"  # pragma: allowlist secret
    )
    assert len(IANA_ROOT_ZONE_TLDS) == len(IANA_ROOT_ZONE_TLD_SET) == 1_438
    assert tuple(sorted(IANA_ROOT_ZONE_TLDS)) == IANA_ROOT_ZONE_TLDS
    assert {"xn--45q11c", "xn--mgb9awbf", "xn--xkc2al3hye2a"} <= IANA_ROOT_ZONE_TLD_SET
    assert {"xn--", "xn", "45q11c", "--mgbt3dhd"}.isdisjoint(IANA_ROOT_ZONE_TLD_SET)


def test_pinned_e164_snapshot_is_complete_and_self_consistent() -> None:
    assert (
        E164_METADATA_COMMIT
        == "99ade73f8465edd4a71969c8899bc45a854ed100"  # pragma: allowlist secret
    )
    assert E164_METADATA_SHA256 == (
        "9d93b18cbaffe4c996abe5ca637633853"  # pragma: allowlist secret
        "01ec899be5d2821d7ed53828447fc0e"  # pragma: allowlist secret
    )
    assert len(E164_ASSIGNED_CALLING_CODE_SET) == 215
    assert E164_POSSIBLE_NATIONAL_LENGTHS.keys() == E164_ASSIGNED_CALLING_CODE_SET
    assert E164_POSSIBLE_NATIONAL_LENGTHS["1"] == {7, 10}
    assert E164_POSSIBLE_NATIONAL_LENGTHS["44"] == {7, 9, 10}
    assert E164_POSSIBLE_NATIONAL_LENGTHS["500"] == {5}
    assert all(
        code.isascii()
        and code.isdecimal()
        and 1 <= len(code) <= 3
        and all(4 <= length <= 17 for length in lengths)
        for code, lengths in E164_POSSIBLE_NATIONAL_LENGTHS.items()
    )


UNSAFE_EMAILS = (
    "a@.example.org",
    ".a@example.org",
    "a..b@example.org",
    "a@example..org",
    "a@-example.org",
    "a@example-.org",
    "a@example.org/path",
    "<victim@example.org>",
    "a\x00@example.org",
    "a\x1b@example.org",
    "a\x9b@example.org",
    "a\u200b@example.org",
    "a\u202e@example.org",
    "a@example.org\x00",
)


def test_committed_schemas_match_executable_contracts(tmp_path: Path) -> None:
    write_schemas(tmp_path)
    expected_documents = schema_documents()
    assert set(path.name for path in tmp_path.iterdir()) == set(expected_documents)
    for filename, expected in expected_documents.items():
        committed = json.loads((SKILL_ROOT / "assets" / filename).read_text(encoding="utf-8"))
        generated = json.loads((tmp_path / filename).read_text(encoding="utf-8"))
        assert committed == generated == expected
        assert (SKILL_ROOT / "assets" / filename).read_bytes() == (tmp_path / filename).read_bytes()


def test_json_schemas_are_valid_and_accept_committed_examples() -> None:
    documents = schema_documents()
    for schema in documents.values():
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        Draft202012Validator.check_schema(schema)

    checker = FormatChecker()
    campaign_validator = Draft202012Validator(
        documents["campaign-brief.schema.json"],
        format_checker=checker,
    )
    donor_validator = Draft202012Validator(
        documents["donor-record.schema.json"],
        format_checker=checker,
    )
    result_validator = Draft202012Validator(
        documents["outreach-result.schema.json"],
        format_checker=checker,
    )
    example_directories = [ROOT / "examples", ROOT / "examples" / "jll-supplied"]
    for example_directory in example_directories:
        campaign = json.loads((example_directory / "campaign.json").read_text(encoding="utf-8"))
        campaign_validator.validate(campaign)
        for line in (example_directory / "donors.jsonl").read_text(encoding="utf-8").splitlines():
            donor_validator.validate(json.loads(line))
        for line in (example_directory / "results.jsonl").read_text(encoding="utf-8").splitlines():
            result_validator.validate(json.loads(line))


def test_json_schema_rejects_runtime_contract_bypasses() -> None:
    documents = schema_documents()
    checker = FormatChecker()
    campaign_validator = Draft202012Validator(
        documents["campaign-brief.schema.json"],
        format_checker=checker,
    )
    donor_validator = Draft202012Validator(
        documents["donor-record.schema.json"],
        format_checker=checker,
    )
    result_validator = Draft202012Validator(
        documents["outreach-result.schema.json"],
        format_checker=checker,
    )

    bad_decimal = campaign_payload()
    bad_decimal["ask_policy"]["minimum"] = "25.00evil"
    with pytest.raises(JsonSchemaValidationError):
        campaign_validator.validate(bad_decimal)

    for invalid_money in (0.001, 0.07, 0.29, 10_000_000_000.00):
        bad_campaign_money = campaign_payload()
        bad_campaign_money["ask_policy"]["minimum"] = invalid_money
        with pytest.raises(JsonSchemaValidationError):
            campaign_validator.validate(bad_campaign_money)
        with pytest.raises(ValidationError):
            CampaignBrief.model_validate(bad_campaign_money)

        bad_donor_money = donor_payload()
        bad_donor_money["giving"].update(
            {
                "last_gift_amount": invalid_money,
                "largest_gift_amount": invalid_money,
                "lifetime_value": invalid_money,
            }
        )
        with pytest.raises(JsonSchemaValidationError):
            donor_validator.validate(bad_donor_money)
        with pytest.raises(ValidationError):
            DonorRecord.model_validate(bad_donor_money)

        bad_result_money = json.loads(
            (ROOT / "examples" / "results.jsonl").read_text(encoding="utf-8").splitlines()[0]
        )
        bad_result_money["draft"]["ask"]["amount"] = invalid_money
        with pytest.raises(JsonSchemaValidationError):
            result_validator.validate(bad_result_money)
        with pytest.raises(ValidationError):
            OutreachResult.model_validate(bad_result_money)

    credential_bearing_url = campaign_payload()
    credential_bearing_url["call_to_action"]["url"] = (
        "https://donate.example.org/path?api_key=secret-value"
    )
    with pytest.raises(JsonSchemaValidationError):
        campaign_validator.validate(credential_bearing_url)

    for unsafe_url in UNSAFE_CTA_URLS:
        bad_url = campaign_payload()
        bad_url["call_to_action"]["url"] = unsafe_url
        with pytest.raises(JsonSchemaValidationError):
            campaign_validator.validate(bad_url)
        with pytest.raises(ValidationError):
            CampaignBrief.model_validate(bad_url)

    mismatched_pair = donor_payload()
    mismatched_pair["giving"]["last_gift_date"] = None
    with pytest.raises(JsonSchemaValidationError):
        donor_validator.validate(mismatched_pair)

    spoofed_donor_source = donor_payload(
        facts=[
            {
                "fact_id": "donor.spoofed",
                "text": "A spoofed provenance fact.",
                "source": "campaign",
                "category": "program",
                "approved_for_outreach": True,
            }
        ]
    )
    with pytest.raises(JsonSchemaValidationError):
        donor_validator.validate(spoofed_donor_source)
    with pytest.raises(ValidationError):
        DonorRecord.model_validate(spoofed_donor_source)

    spoofed_campaign_source = campaign_payload()
    spoofed_campaign_source["facts"][0]["source"] = "crm"
    with pytest.raises(JsonSchemaValidationError):
        campaign_validator.validate(spoofed_campaign_source)
    with pytest.raises(ValidationError):
        CampaignBrief.model_validate(spoofed_campaign_source)

    spoofed_donor_namespace = donor_payload(
        facts=[
            {
                "fact_id": "campaign.spoof",
                "text": "A namespace-spoofed donor fact.",
                "source": "crm",
                "category": "program",
                "approved_for_outreach": True,
            }
        ]
    )
    with pytest.raises(JsonSchemaValidationError):
        donor_validator.validate(spoofed_donor_namespace)
    with pytest.raises(ValidationError):
        DonorRecord.model_validate(spoofed_donor_namespace)

    for fact_id, source in (
        ("crm.spoof", "campaign"),
        ("donor.spoof", "organization"),
        ("organization.spoof", "campaign"),
    ):
        spoofed_campaign_namespace = campaign_payload()
        spoofed_campaign_namespace["facts"][0]["fact_id"] = fact_id
        spoofed_campaign_namespace["facts"][0]["source"] = source
        with pytest.raises(JsonSchemaValidationError):
            campaign_validator.validate(spoofed_campaign_namespace)
        with pytest.raises(ValidationError):
            CampaignBrief.model_validate(spoofed_campaign_namespace)

    contradictory_result = json.loads(
        (ROOT / "examples" / "results.jsonl").read_text(encoding="utf-8").splitlines()[0]
    )
    contradictory_result["draft"] = None
    contradictory_result["audit"]["provider_called"] = False
    contradictory_result["audit"]["provider_name"] = None
    with pytest.raises(JsonSchemaValidationError):
        result_validator.validate(contradictory_result)


@pytest.mark.parametrize("amount", ["0.07", "0.29", "9999999999.99"])
def test_canonical_money_strings_have_schema_runtime_parity(amount: str) -> None:
    payload = donor_payload(
        giving={
            "currency": "USD",
            "last_gift_amount": amount,
            "largest_gift_amount": amount,
            "lifetime_value": amount,
            "last_gift_date": "2026-01-10",
        }
    )
    validator = Draft202012Validator(
        schema_documents()["donor-record.schema.json"],
        format_checker=FormatChecker(),
    )
    validator.validate(payload)
    assert DonorRecord.model_validate(payload).giving.last_gift_amount is not None


@pytest.mark.parametrize(
    "amount",
    [
        "1e2",
        "1e-2",
        "0.1e2",
        "100e-2",
        "1.0",
        "01.00",
        "0.00",
        "1.\u0662\u0665",
        "1\u0662.25",
        "2\u0665.00",
    ],
)
def test_noncanonical_money_strings_fail_schema_and_runtime(amount: str) -> None:
    payload = campaign_payload()
    payload["ask_policy"]["minimum"] = amount
    validator = Draft202012Validator(
        schema_documents()["campaign-brief.schema.json"],
        format_checker=FormatChecker(),
    )
    with pytest.raises(JsonSchemaValidationError):
        validator.validate(payload)
    with pytest.raises(ValidationError):
        CampaignBrief.model_validate(payload)


@pytest.mark.parametrize("currency", ["ABC", "XYZ", "ZZZ", "usd"])
def test_currency_codes_must_be_active_iso_4217_in_schema_and_runtime(currency: str) -> None:
    campaign_document = campaign_payload()
    campaign_document["ask_policy"]["currency"] = currency
    donor_document = donor_payload()
    donor_document["giving"]["currency"] = currency

    documents = schema_documents()
    pairs = (
        (campaign_document, documents["campaign-brief.schema.json"], CampaignBrief),
        (donor_document, documents["donor-record.schema.json"], DonorRecord),
    )
    for payload, schema, model in pairs:
        with pytest.raises(JsonSchemaValidationError):
            Draft202012Validator(schema, format_checker=FormatChecker()).validate(payload)
        with pytest.raises(ValidationError):
            model.model_validate(payload)


@pytest.mark.parametrize("multiplier", ["1.\u0662\u0665", "\u0661.25"])
def test_non_ascii_multiplier_digits_fail_schema_and_runtime(multiplier: str) -> None:
    payload = campaign_payload()
    payload["ask_policy"]["multiplier"] = multiplier
    validator = Draft202012Validator(
        schema_documents()["campaign-brief.schema.json"],
        format_checker=FormatChecker(),
    )
    with pytest.raises(JsonSchemaValidationError):
        validator.validate(payload)
    with pytest.raises(ValidationError):
        CampaignBrief.model_validate(payload)


@pytest.mark.parametrize("unsafe_date", [0, "2026-08-01T00:00:00", "2026-02-30"])
def test_dates_require_exact_valid_yyyy_mm_dd_in_schema_and_runtime(
    unsafe_date: object,
) -> None:
    campaign_document = campaign_payload()
    campaign_document["as_of_date"] = unsafe_date
    campaign_validator = Draft202012Validator(
        schema_documents()["campaign-brief.schema.json"],
        format_checker=FormatChecker(),
    )
    with pytest.raises(JsonSchemaValidationError):
        campaign_validator.validate(campaign_document)
    with pytest.raises(ValidationError):
        CampaignBrief.model_validate(campaign_document)

    donor_document = donor_payload(last_contact_date=unsafe_date)
    donor_document["giving"]["last_gift_date"] = unsafe_date
    donor_validator = Draft202012Validator(
        schema_documents()["donor-record.schema.json"],
        format_checker=FormatChecker(),
    )
    with pytest.raises(JsonSchemaValidationError):
        donor_validator.validate(donor_document)
    with pytest.raises(ValidationError):
        DonorRecord.model_validate(donor_document)

    result_document = json.loads(
        (ROOT / "examples" / "results.jsonl").read_text(encoding="utf-8").splitlines()[0]
    )
    result_document["audit"]["evaluated_on"] = unsafe_date
    result_validator = Draft202012Validator(
        schema_documents()["outreach-result.schema.json"],
        format_checker=FormatChecker(),
    )
    with pytest.raises(JsonSchemaValidationError):
        result_validator.validate(result_document)
    with pytest.raises(ValidationError):
        OutreachResult.model_validate(result_document)


@pytest.mark.parametrize(
    ("document_name", "path"),
    [
        ("donor", ("donor_id",)),
        ("donor", ("email",)),
        ("donor", ("giving", "currency")),
        ("donor", ("giving", "last_gift_amount")),
        ("campaign", ("campaign_id",)),
        ("campaign", ("call_to_action", "url")),
        ("campaign", ("ask_policy", "currency")),
        ("campaign", ("ask_policy", "multiplier")),
        ("campaign", ("ask_policy", "minimum")),
        ("campaign", ("as_of_date",)),
        ("result", ("audit", "input_fingerprint")),
        ("result", ("draft", "ask", "currency")),
        ("result", ("draft", "ask", "amount")),
    ],
)
def test_anchored_schema_patterns_reject_terminal_line_feed(
    document_name: str,
    path: tuple[str, ...],
) -> None:
    if document_name == "donor":
        payload = donor_payload()
        model = DonorRecord
        schema_name = "donor-record.schema.json"
    elif document_name == "campaign":
        payload = campaign_payload()
        model = CampaignBrief
        schema_name = "campaign-brief.schema.json"
    else:
        payload = json.loads(
            (ROOT / "examples" / "results.jsonl").read_text(encoding="utf-8").splitlines()[0]
        )
        model = OutreachResult
        schema_name = "outreach-result.schema.json"

    target = payload
    for part in path[:-1]:
        target = target[part]
    current = target[path[-1]]
    assert isinstance(current, str)
    target[path[-1]] = f"{current}\n"

    validator = Draft202012Validator(
        schema_documents()[schema_name],
        format_checker=FormatChecker(),
    )
    with pytest.raises(JsonSchemaValidationError):
        validator.validate(payload)
    with pytest.raises(ValidationError):
        model.model_validate(payload)


def test_output_contract_enforces_channel_and_draft_shape_in_schema_and_runtime() -> None:
    documents = schema_documents()
    validator = Draft202012Validator(
        documents["outreach-result.schema.json"],
        format_checker=FormatChecker(),
    )
    results = [
        json.loads(line)
        for line in (ROOT / "examples" / "results.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    email = next(result for result in results if result["status"] == "draft_ready")
    letter = next(
        result
        for result in results
        if result["status"] == "draft_ready" and result["channel"] == "letter"
    )

    mutations: list[dict[str, Any]] = []
    missing_email_subject = deepcopy(email)
    missing_email_subject["draft"]["subject_line"] = None
    mutations.append(missing_email_subject)
    letter_with_subject = deepcopy(letter)
    letter_with_subject["draft"]["subject_line"] = "Unexpected subject"
    mutations.append(letter_with_subject)
    empty_subject = deepcopy(email)
    empty_subject["draft"]["subject_line"] = ""
    mutations.append(empty_subject)
    empty_body = deepcopy(email)
    empty_body["draft"]["body"] = ""
    mutations.append(empty_body)
    invalid_fact_id = deepcopy(email)
    invalid_fact_id["draft"]["fact_ids_used"] = [" space "]
    mutations.append(invalid_fact_id)
    too_many_fact_ids = deepcopy(email)
    too_many_fact_ids["draft"]["fact_ids_used"] = [f"fact.{index}" for index in range(26)]
    mutations.append(too_many_fact_ids)

    for payload in mutations:
        with pytest.raises(JsonSchemaValidationError):
            validator.validate(payload)
        with pytest.raises(ValidationError):
            OutreachResult.model_validate(payload)


def test_output_contract_rejects_unknown_machine_vocabulary() -> None:
    validator = Draft202012Validator(
        schema_documents()["outreach-result.schema.json"],
        format_checker=FormatChecker(),
    )
    results = [
        json.loads(line)
        for line in (ROOT / "examples" / "results.jsonl").read_text(encoding="utf-8").splitlines()
    ]

    unknown_reason = deepcopy(next(result for result in results if result["status"] == "blocked"))
    unknown_reason["reason_codes"] = ["made_up_reason"]

    quality_result = deepcopy(
        next(result for result in results if result["status"] == "draft_ready")
    )
    quality_result.update(
        {
            "status": "quality_rejected",
            "review_required": True,
            "reason_codes": ["draft_failed_quality_gate"],
            "quality_issues": [
                {
                    "code": "unapproved_url",
                    "message": "draft contains an unapproved URL",
                }
            ],
            "draft": None,
        }
    )
    validator.validate(quality_result)
    OutreachResult.model_validate(quality_result)
    unknown_quality = deepcopy(quality_result)
    unknown_quality["quality_issues"][0]["code"] = "made_up_quality"

    for payload in (unknown_reason, unknown_quality):
        with pytest.raises(JsonSchemaValidationError):
            validator.validate(payload)
        with pytest.raises(ValidationError):
            OutreachResult.model_validate(payload)


def test_fact_provenance_identifiers_are_namespaced_across_candidate_and_result() -> None:
    with pytest.raises(ValidationError):
        DraftCandidate.model_validate(
            {
                "subject_line": "Subject",
                "salutation": "Hi Maya,",
                "body": "Hi Maya,\n\nBody.",
                "fact_ids_used": ["x"],
            }
        )

    payload = json.loads(
        (ROOT / "examples" / "results.jsonl").read_text(encoding="utf-8").splitlines()[0]
    )
    validator = Draft202012Validator(
        schema_documents()["outreach-result.schema.json"],
        format_checker=FormatChecker(),
    )
    invalid_fact_reference = deepcopy(payload)
    invalid_fact_reference["draft"]["fact_ids_used"] = ["x"]
    invalid_audit_reference = deepcopy(payload)
    invalid_audit_reference["audit"]["excluded_fact_ids"] = ["x"]

    for invalid in (invalid_fact_reference, invalid_audit_reference):
        with pytest.raises(JsonSchemaValidationError):
            validator.validate(invalid)
        with pytest.raises(ValidationError):
            OutreachResult.model_validate(invalid)

    payload["audit"]["excluded_fact_ids"] = ["redacted.sensitive-fact-id"]
    validator.validate(payload)
    OutreachResult.model_validate(payload)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("record_index",), "1"),
        (("review_required",), "false"),
        (("audit", "provider_called"), 1),
    ],
)
def test_output_scalar_types_are_strict_in_schema_and_runtime(
    path: tuple[str, ...],
    value: Any,
) -> None:
    payload = json.loads(
        (ROOT / "examples" / "results.jsonl").read_text(encoding="utf-8").splitlines()[0]
    )
    target = payload
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value
    validator = Draft202012Validator(
        schema_documents()["outreach-result.schema.json"],
        format_checker=FormatChecker(),
    )
    with pytest.raises(JsonSchemaValidationError):
        validator.validate(payload)
    with pytest.raises(ValidationError):
        OutreachResult.model_validate(payload)


@pytest.mark.parametrize(
    "unsafe_text",
    [
        "\x00",
        "\x1b[31m",
        "\x85",
        "\x9b",
        "\u034f",
        "\ud800",
        "\u200b",
        "\u202eoverride",
        "\u206a",
        "\ufe0f",
        "\U00013439",
        "\U0001ccd6",
        "\U000e0100",
        "\ufdd0",
        "\ufffe",
        "\U0001fffe",
        "\u0378",
        "\ue000",
        "\U000f0000",
        "\U00100000",
    ],
)
def test_output_body_rejects_control_and_format_text_in_schema_and_runtime(
    unsafe_text: str,
) -> None:
    payload = json.loads(
        (ROOT / "examples" / "results.jsonl").read_text(encoding="utf-8").splitlines()[0]
    )
    payload["draft"]["body"] += unsafe_text
    validator = Draft202012Validator(
        schema_documents()["outreach-result.schema.json"],
        format_checker=FormatChecker(),
    )
    with pytest.raises(JsonSchemaValidationError):
        validator.validate(payload)
    with pytest.raises(ValidationError):
        OutreachResult.model_validate(payload)


def test_runtime_result_contract_rejects_contradictory_envelopes() -> None:
    payload = json.loads(
        (ROOT / "examples" / "results.jsonl").read_text(encoding="utf-8").splitlines()[0]
    )
    payload["draft"] = None
    payload["audit"]["provider_called"] = False
    payload["audit"]["provider_name"] = None
    with pytest.raises(ValidationError):
        OutreachResult.model_validate(payload)


@pytest.mark.parametrize(
    "mutation",
    [
        "provider_call",
        "review_flag",
        "channel",
        "donor_id",
        "validation_issues",
        "quality_issues",
        "ready_reason",
        "provider_name",
        "excluded_fact_ids",
        "draft_fact_ids",
    ],
)
def test_runtime_result_contract_rejects_each_envelope_contradiction(
    mutation: str,
) -> None:
    payload = json.loads(
        (ROOT / "examples" / "results.jsonl").read_text(encoding="utf-8").splitlines()[0]
    )
    if mutation == "provider_call":
        payload["audit"]["provider_called"] = False
        payload["audit"]["provider_name"] = None
    elif mutation == "review_flag":
        payload["review_required"] = True
    elif mutation == "channel":
        payload["channel"] = None
    elif mutation == "donor_id":
        payload["donor_id"] = None
    elif mutation == "validation_issues":
        payload["validation_issues"] = [{"field": "x", "code": "invalid", "message": "invalid"}]
    elif mutation == "quality_issues":
        payload["quality_issues"] = [{"code": "unsafe", "message": "unsafe"}]
    elif mutation == "ready_reason":
        payload["reason_codes"] = ["unexpected_reason"]
    elif mutation == "provider_name":
        payload["audit"]["provider_name"] = None
    elif mutation == "excluded_fact_ids":
        payload["audit"]["excluded_fact_ids"] = ["fact.one", "fact.one"]
    elif mutation == "draft_fact_ids":
        payload["draft"]["fact_ids_used"] = ["fact.one", "fact.one"]
    else:  # pragma: no cover - parametrization is closed above
        raise AssertionError(mutation)

    with pytest.raises(ValidationError):
        OutreachResult.model_validate(payload)


def test_runtime_result_contract_rejects_missing_or_duplicate_failure_codes() -> None:
    results = [
        json.loads(line)
        for line in (ROOT / "examples" / "results.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    suppressed = next(result for result in results if result["status"] == "suppressed")
    suppressed["reason_codes"] = []
    with pytest.raises(ValidationError):
        OutreachResult.model_validate(suppressed)

    suppressed["reason_codes"] = ["do_not_contact", "do_not_contact"]
    with pytest.raises(ValidationError):
        OutreachResult.model_validate(suppressed)

    ready = next(result for result in results if result["status"] == "draft_ready")
    ready.update(
        {
            "status": "quality_rejected",
            "review_required": True,
            "reason_codes": ["draft_failed_quality_gate"],
            "quality_issues": [
                {"code": "unsafe", "message": "first"},
                {"code": "unsafe", "message": "second"},
            ],
            "draft": None,
        }
    )
    with pytest.raises(ValidationError):
        OutreachResult.model_validate(ready)


def test_result_schema_rejects_duplicate_reason_and_exclusion_codes() -> None:
    validator = Draft202012Validator(
        schema_documents()["outreach-result.schema.json"],
        format_checker=FormatChecker(),
    )
    results = [
        json.loads(line)
        for line in (ROOT / "examples" / "results.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    suppressed = next(result for result in results if result["status"] == "suppressed")
    suppressed["reason_codes"] = ["do_not_contact", "do_not_contact"]
    with pytest.raises(JsonSchemaValidationError):
        validator.validate(suppressed)

    reviewed = next(result for result in results if result["status"] == "review_required")
    reviewed["audit"]["excluded_fact_ids"] = ["fact.one", "fact.one"]
    with pytest.raises(JsonSchemaValidationError):
        validator.validate(reviewed)


def test_skill_structure_and_frontmatter_are_portable() -> None:
    skill_path = SKILL_ROOT / "SKILL.md"
    lines = skill_path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "---"
    closing = lines.index("---", 1)
    frontmatter = lines[1:closing]
    assert [line.split(":", 1)[0] for line in frontmatter] == [
        "name",
        "description",
    ]
    assert frontmatter[0] == f"name: {SKILL_ROOT.name}"
    assert 1 <= len(frontmatter[1].split(":", 1)[1].strip()) <= 1024
    assert len(lines) < 500
    assert "TODO" not in skill_path.read_text(encoding="utf-8")

    linked_paths = re.findall(r"\[[^\]]+\]\(([^)]+)\)", "\n".join(lines))
    for linked_path in linked_paths:
        assert (SKILL_ROOT / linked_path).exists(), linked_path


def test_repository_skill_validator_passes() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "validate_skill.py"),
            str(SKILL_ROOT),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.startswith("validated:")


def test_openai_interface_metadata_matches_skill() -> None:
    metadata = (SKILL_ROOT / "agents" / "openai.yaml").read_text(encoding="utf-8")
    assert 'display_name: "Charity Donor Outreach"' in metadata
    assert 'short_description: "Safe, reviewable donor outreach drafts"' in metadata
    assert "$charity-donor-outreach" in metadata


def test_documented_machine_vocabulary_matches_runtime_enums() -> None:
    contract = (SKILL_ROOT / "references" / "OUTPUT_CONTRACT.md").read_text(encoding="utf-8")
    assert {f"`{code.value}`" for code in ReasonCode}.issubset(
        set(re.findall(r"`[^`]+`", contract))
    )
    assert {f"`{code.value}`" for code in QualityCode}.issubset(
        set(re.findall(r"`[^`]+`", contract))
    )


def test_architecture_source_matches_embedded_mermaid() -> None:
    source = (ROOT / "docs" / "architecture-flow.mmd").read_text(encoding="utf-8").strip()
    document = (ROOT / "docs" / "ARCHITECTURE.md").read_text(encoding="utf-8")
    assert source in document


def test_local_markdown_links_resolve() -> None:
    link_pattern = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")
    for document in [
        ROOT / "README.md",
        ROOT / "ASSESSMENT.md",
        ROOT / "docs" / "ARCHITECTURE.md",
        ROOT / "docs" / "QUALITY.md",
        SKILL_ROOT / "SKILL.md",
    ]:
        for target in link_pattern.findall(document.read_text(encoding="utf-8")):
            if target.startswith(("https://", "http://", "#")):
                continue
            clean_target = target.split("#", 1)[0]
            assert (document.parent / clean_target).resolve().exists(), (
                document,
                target,
            )


def test_public_surface_contains_source_artifacts_only() -> None:
    excluded_roots = {
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        ".venv-reviewer",
        "build",
        "dist",
        "out",
    }
    visible_files = [
        path
        for path in ROOT.rglob("*")
        if path.is_file() and excluded_roots.isdisjoint(path.relative_to(ROOT).parts)
    ]
    forbidden_suffixes = {".html", ".zip"}
    assert not [path for path in visible_files if path.suffix.casefold() in forbidden_suffixes]


def test_examples_use_reserved_email_domains_only() -> None:
    donor_files = [
        ROOT / "examples" / "donors.jsonl",
        ROOT / "examples" / "jll-supplied" / "donors.jsonl",
    ]
    donors_text = "\n".join(path.read_text(encoding="utf-8") for path in donor_files)
    addresses = re.findall(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+", donors_text)
    assert addresses
    assert all(address.endswith("@example.org") for address in addresses)


def test_provider_contract_omits_raw_contact_and_policy_fields() -> None:
    forbidden = {
        "donor_id",
        "email",
        "postal_address",
        "channel_consent",
        "do_not_contact",
        "giving",
        "last_contact_date",
    }
    assert forbidden.isdisjoint(DraftRequest.model_fields)


@pytest.mark.parametrize(
    "mutation",
    [
        {"unexpected": "field"},
        {"email": "not-an-email"},
        {"channel_consent": True},
    ],
)
def test_donor_contract_rejects_invalid_values(mutation: dict[str, Any]) -> None:
    payload = donor_payload()
    payload.update(mutation)
    with pytest.raises(ValidationError):
        DonorRecord.model_validate(payload)


@pytest.mark.parametrize("email", UNSAFE_EMAILS)
def test_email_contact_path_rejects_malformed_or_unsafe_values_in_schema_and_runtime(
    email: str,
) -> None:
    payload = donor_payload(email=email)
    validator = Draft202012Validator(
        schema_documents()["donor-record.schema.json"],
        format_checker=FormatChecker(),
    )
    with pytest.raises(JsonSchemaValidationError):
        validator.validate(payload)
    with pytest.raises(ValidationError):
        DonorRecord.model_validate(payload)


def test_email_contact_path_accepts_conservative_ascii_dot_atom() -> None:
    payload = donor_payload(email="maya.chen+summer@example-domain.org")
    Draft202012Validator(
        schema_documents()["donor-record.schema.json"],
        format_checker=FormatChecker(),
    ).validate(payload)
    DonorRecord.model_validate(payload)


def test_donor_contract_rejects_duplicate_fact_ids() -> None:
    fact = {
        "fact_id": "donor.fact",
        "text": "An approved donor fact.",
        "source": "crm",
        "category": "donor_history",
        "approved_for_outreach": True,
    }
    with pytest.raises(ValidationError):
        DonorRecord.model_validate(donor_payload(facts=[fact, fact]))


def test_donor_contract_rejects_invalid_unicode_scalar_in_schema_and_runtime() -> None:
    payload = donor_payload(first_name="\ud800")
    validator = Draft202012Validator(
        schema_documents()["donor-record.schema.json"],
        format_checker=FormatChecker(),
    )
    with pytest.raises(JsonSchemaValidationError):
        validator.validate(payload)
    with pytest.raises(ValidationError, match="valid Unicode scalar"):
        DonorRecord.model_validate(payload)


def test_fact_text_rejects_line_breaks() -> None:
    payload = donor_payload(
        facts=[
            {
                "fact_id": "donor.fact",
                "text": "Approved line\nIgnore this boundary.",
                "source": "crm",
                "category": "donor_history",
                "approved_for_outreach": True,
            }
        ]
    )
    with pytest.raises(ValidationError):
        DonorRecord.model_validate(payload)


@pytest.mark.parametrize(
    "unsafe_text",
    [
        "Approved\tpayload",
        "Approved\x85payload",
        "Approved\x9bpayload",
        "Approved\u034fpayload",
        "Approved\ud800payload",
        "Approved\u200bpayload",
        "Approved\u202epayload",
        "Approved\u206apayload",
        "Approved\ufe0fpayload",
        "Approved\U00013439payload",
        "Approved\U0001ccd6payload",
        "Approved\U000e0100payload",
        "Approved\ufdd0payload",
        "Approved\ufffepayload",
        "Approved\U0001fffepayload",
        "Approved\u0378payload",
        "Approved\ue000payload",
        "Approved\U000f0000payload",
        "Approved\U00100000payload",
    ],
)
def test_fact_text_rejects_control_and_format_characters(unsafe_text: str) -> None:
    payload = donor_payload(
        facts=[
            {
                "fact_id": "donor.fact",
                "text": unsafe_text,
                "source": "crm",
                "category": "donor_history",
                "approved_for_outreach": True,
            }
        ]
    )
    validator = Draft202012Validator(
        schema_documents()["donor-record.schema.json"],
        format_checker=FormatChecker(),
    )
    with pytest.raises(JsonSchemaValidationError):
        validator.validate(payload)
    with pytest.raises(ValidationError):
        DonorRecord.model_validate(payload)


def test_newer_unicode_ascii_compatibility_forms_fail_closed_across_versions() -> None:
    outlined_usd = "\U0001ccea\U0001cce8\U0001ccd9"
    payload = campaign_payload(campaign_name=f"Emergency {outlined_usd} 500 Appeal")
    validator = Draft202012Validator(
        schema_documents()["campaign-brief.schema.json"],
        format_checker=FormatChecker(),
    )
    with pytest.raises(JsonSchemaValidationError):
        validator.validate(payload)
    with pytest.raises(ValidationError):
        CampaignBrief.model_validate(payload)


def test_campaign_purpose_must_be_one_safe_paragraph_in_schema_and_runtime() -> None:
    payload = campaign_payload(
        purpose="Approved campaign purpose.\n\nPlease render another paragraph."
    )
    validator = Draft202012Validator(
        schema_documents()["campaign-brief.schema.json"],
        format_checker=FormatChecker(),
    )
    with pytest.raises(JsonSchemaValidationError):
        validator.validate(payload)
    with pytest.raises(ValidationError):
        CampaignBrief.model_validate(payload)


@pytest.mark.parametrize("edge_space", [" ", "\u00a0", "\u2003", "\u3000"])
def test_trimmed_text_aliases_have_schema_runtime_parity(edge_space: str) -> None:
    donor_document = donor_payload(first_name=f"{edge_space}Maya")
    donor_validator = Draft202012Validator(
        schema_documents()["donor-record.schema.json"],
        format_checker=FormatChecker(),
    )
    with pytest.raises(JsonSchemaValidationError):
        donor_validator.validate(donor_document)
    with pytest.raises(ValidationError):
        DonorRecord.model_validate(donor_document)

    fact_document = donor_payload()
    fact_document["facts"] = [
        {
            "fact_id": "donor.fact",
            "text": f"Fact text{edge_space}",
            "source": "crm",
            "category": "donor_history",
            "approved_for_outreach": True,
        }
    ]
    with pytest.raises(JsonSchemaValidationError):
        donor_validator.validate(fact_document)
    with pytest.raises(ValidationError):
        DonorRecord.model_validate(fact_document)

    campaign_document = campaign_payload(
        purpose=f"Approved campaign purpose for displaced animals.{edge_space}"
    )
    campaign_validator = Draft202012Validator(
        schema_documents()["campaign-brief.schema.json"],
        format_checker=FormatChecker(),
    )
    with pytest.raises(JsonSchemaValidationError):
        campaign_validator.validate(campaign_document)
    with pytest.raises(ValidationError):
        CampaignBrief.model_validate(campaign_document)

    result_document = json.loads(
        (ROOT / "examples" / "results.jsonl").read_text(encoding="utf-8").splitlines()[0]
    )
    result_document["draft"]["subject_line"] = f"{edge_space}Subject"
    result_validator = Draft202012Validator(
        schema_documents()["outreach-result.schema.json"],
        format_checker=FormatChecker(),
    )
    with pytest.raises(JsonSchemaValidationError):
        result_validator.validate(result_document)
    with pytest.raises(ValidationError):
        OutreachResult.model_validate(result_document)


@pytest.mark.parametrize("url", UNSAFE_CTA_URLS)
def test_campaign_requires_safe_https_call_to_action(url: str) -> None:
    payload = campaign_payload()
    payload["call_to_action"]["url"] = url
    with pytest.raises(ValidationError):
        CampaignBrief.model_validate(payload)


@pytest.mark.parametrize("terminal", [".", "!", ";", ":"])
def test_safe_cta_terminal_punctuation_has_schema_runtime_parity(terminal: str) -> None:
    payload = campaign_payload()
    payload["call_to_action"]["url"] = f"https://donate.example.org/path{terminal}"
    Draft202012Validator(
        schema_documents()["campaign-brief.schema.json"],
        format_checker=FormatChecker(),
    ).validate(payload)
    CampaignBrief.model_validate(payload)


def test_campaign_rejects_ask_bounds_and_duplicate_fact_ids() -> None:
    bad_bounds = campaign_payload()
    bad_bounds["ask_policy"]["minimum"] = "100.00"
    bad_bounds["ask_policy"]["maximum"] = "25.00"
    with pytest.raises(ValidationError):
        CampaignBrief.model_validate(bad_bounds)

    duplicate_facts = campaign_payload()
    duplicate_facts["facts"] = [
        duplicate_facts["facts"][0],
        duplicate_facts["facts"][0],
    ]
    with pytest.raises(ValidationError):
        CampaignBrief.model_validate(duplicate_facts)

    duplicate_segments = campaign_payload()
    duplicate_segments["review_policy"]["segments"] = ["major", "major"]
    with pytest.raises(ValidationError):
        CampaignBrief.model_validate(duplicate_segments)
