"""Strict input, policy, provider, and output contracts."""

from __future__ import annotations

import re
import unicodedata
from bisect import bisect_right
from collections.abc import Mapping, Sequence
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any, Literal, Self, cast
from urllib.parse import urlsplit

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    WithJsonSchema,
    field_validator,
    model_validator,
)

from ._iso4217 import ISO_4217_ACTIVE_CODE_SET, ISO_4217_ACTIVE_CODES
from ._unicode14 import UNICODE_14_UNASSIGNED_OR_PRIVATE_USE_RANGES

_UNSAFE_INVISIBLE_CODE_POINT_RANGES = (
    (0x00AD, 0x00AD),
    (0x034F, 0x034F),
    (0x0600, 0x0605),
    (0x061C, 0x061C),
    (0x06DD, 0x06DD),
    (0x070F, 0x070F),
    (0x0890, 0x0891),
    (0x08E2, 0x08E2),
    (0x115F, 0x1160),
    (0x17B4, 0x17B5),
    (0x180B, 0x180F),
    (0x200B, 0x200F),
    (0x202A, 0x202E),
    (0x2060, 0x206F),
    (0x3164, 0x3164),
    (0xFE00, 0xFE0F),
    (0xFEFF, 0xFEFF),
    (0xFFA0, 0xFFA0),
    (0xFFF0, 0xFFFB),
    (0x110BD, 0x110BD),
    (0x110CD, 0x110CD),
    (0x13430, 0x1343F),
    (0x1BCA0, 0x1BCA3),
    (0x1CCD6, 0x1CCF9),
    (0x1D173, 0x1D17A),
    (0xE0000, 0xE0FFF),
)


def _json_regex_code_point(code_point: int) -> str:
    return rf"\u{code_point:04X}" if code_point <= 0xFFFF else chr(code_point)


_UNSAFE_INVISIBLE_RANGES = "".join(
    _json_regex_code_point(start)
    if start == end
    else f"{_json_regex_code_point(start)}-{_json_regex_code_point(end)}"
    for start, end in _UNSAFE_INVISIBLE_CODE_POINT_RANGES
)
_UNICODE_14_UNSAFE_RANGE_STARTS = tuple(
    start for start, _ in UNICODE_14_UNASSIGNED_OR_PRIVATE_USE_RANGES
)
_UNICODE_14_UNSAFE_JSON_RANGES = "".join(
    _json_regex_code_point(start)
    if start == end
    else f"{_json_regex_code_point(start)}-{_json_regex_code_point(end)}"
    for start, end in UNICODE_14_UNASSIGNED_OR_PRIVATE_USE_RANGES
)
_TRIM_WHITESPACE_RANGES = (
    r"\u0009-\u000D\u001C-\u0020\u0085\u00A0\u1680\u2000-\u200A"
    r"\u2028-\u2029\u202F\u205F\u3000"
)
_SINGLE_LINE_JSON_PATTERN = (
    rf"^(?![{_TRIM_WHITESPACE_RANGES}])(?!.*[{_TRIM_WHITESPACE_RANGES}]$)"
    rf"[^\u0000-\u001F\u007F-\u009F\u2028-\u2029\uD800-\uDFFF"
    rf"{_UNSAFE_INVISIBLE_RANGES}{_UNICODE_14_UNSAFE_JSON_RANGES}]+$(?![\s\S])"
)
_MULTILINE_JSON_PATTERN = (
    rf"^[^\u0000-\u0009\u000B-\u001F\u007F-\u009F\u2028-\u2029\uD800-\uDFFF"
    rf"{_UNSAFE_INVISIBLE_RANGES}{_UNICODE_14_UNSAFE_JSON_RANGES}]+$(?![\s\S])"
)
_CANONICAL_MONEY_PATTERN = r"^(?!0\.00$)(?:0|[1-9][0-9]{0,9})\.[0-9]{2}$(?![\s\S])"
_CANONICAL_MULTIPLIER_PATTERN = r"^(?:0\.(?!00)[0-9]{2}|[1-4]\.[0-9]{2}|5\.00)$(?![\s\S])"
_CANONICAL_DATE_PATTERN = r"^[0-9]{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12][0-9]|3[01])$(?![\s\S])"
_IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]*$"
_IDENTIFIER_JSON_PATTERN = rf"{_IDENTIFIER_PATTERN}(?![\s\S])"
_FACT_IDENTIFIER_PATTERN = r"^(?:campaign|organization|donor|crm)\.[a-z0-9][a-z0-9-]*$"
_FACT_IDENTIFIER_JSON_PATTERN = rf"{_FACT_IDENTIFIER_PATTERN}(?![\s\S])"
_COUNTRY_CODE_PATTERN = r"^[A-Z]{2}$"
_COUNTRY_CODE_JSON_PATTERN = rf"{_COUNTRY_CODE_PATTERN}(?![\s\S])"
_FINGERPRINT_PATTERN = r"^[a-f0-9]{64}$"
_FINGERPRINT_JSON_PATTERN = rf"{_FINGERPRINT_PATTERN}(?![\s\S])"
_EMAIL_ATOM = r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+"
_EMAIL_JSON_PATTERN = (
    rf"^(?=.{{3,254}}$)(?=.{{1,64}}@){_EMAIL_ATOM}(?:\.{_EMAIL_ATOM})*@"
    r"(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+"
    r"[A-Za-z](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$(?![\s\S])"
)
_CTA_URL_JSON_PATTERN = (
    r"^https://(?=[^/]{1,253}(?:/|$))"
    r"(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+"
    r"(?=[A-Za-z0-9-]*[A-Za-z])"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:/[A-Za-z0-9._~!$&'()*+,;=:@/-]*)?$(?![\s\S])"
)
_HOST_LABEL_PATTERN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")


def _validate_trimmed_text(value: Any) -> Any:
    if isinstance(value, str) and value != value.strip():
        raise ValueError("value must not contain leading or trailing whitespace")
    return value


def _validate_single_line_text(value: str) -> str:
    if any(is_unsafe_text_character(character) for character in value):
        raise ValueError(
            "value must be single-line assigned text without control, format, or private-use "
            "characters"
        )
    return value


def _validate_multiline_text(value: str) -> str:
    if any(character != "\n" and is_unsafe_text_character(character) for character in value):
        raise ValueError(
            "value may contain LF line breaks but not control, format, unassigned, or "
            "private-use characters"
        )
    return value


def is_unsafe_text_character(character: str) -> bool:
    """Apply a stable Unicode 14 fail-closed text-safety baseline."""

    code_point = ord(character)
    baseline_index = bisect_right(_UNICODE_14_UNSAFE_RANGE_STARTS, code_point) - 1
    is_unassigned_or_private_use = (
        baseline_index >= 0
        and code_point <= (UNICODE_14_UNASSIGNED_OR_PRIVATE_USE_RANGES[baseline_index][1])
    )
    unsafe_invisible = any(
        start <= code_point <= end for start, end in _UNSAFE_INVISIBLE_CODE_POINT_RANGES
    )
    return (
        is_unassigned_or_private_use
        or unsafe_invisible
        or unicodedata.category(character)
        in {
            "Cc",
            "Cf",
            "Cs",
            "Zl",
            "Zp",
        }
    )


def _parse_canonical_money(value: Any) -> Decimal:
    if not isinstance(value, str) or re.fullmatch(_CANONICAL_MONEY_PATTERN, value) is None:
        raise ValueError("money must be a positive canonical decimal string with two places")
    return Decimal(value)


def _parse_canonical_multiplier(value: Any) -> Decimal:
    if not isinstance(value, str) or re.fullmatch(_CANONICAL_MULTIPLIER_PATTERN, value) is None:
        raise ValueError("multiplier must be a canonical decimal string from 0.01 through 5.00")
    return Decimal(value)


def _validate_currency_code(value: Any) -> str:
    if type(value) is not str or value not in ISO_4217_ACTIVE_CODE_SET:
        raise ValueError("currency must be an active ISO 4217 List One code")
    return value


def _parse_canonical_date(value: Any) -> date:
    if not isinstance(value, str) or re.fullmatch(_CANONICAL_DATE_PATTERN, value) is None:
        raise ValueError("date must be an exact YYYY-MM-DD string")
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise ValueError("date must be a valid Gregorian calendar date") from error


def _validate_email_address(value: Any) -> str:
    if (
        not isinstance(value, str)
        or not value.isascii()
        or re.fullmatch(_EMAIL_JSON_PATTERN, value) is None
    ):
        raise ValueError("email must be a canonical ASCII dot-atom address on a valid DNS domain")
    local_part, domain = value.rsplit("@", maxsplit=1)
    if len(local_part) > 64 or len(domain) > 253:
        raise ValueError("email local part or domain exceeds its maximum length")
    return value


Identifier = Annotated[
    str,
    BeforeValidator(_validate_trimmed_text),
    Field(
        min_length=1,
        max_length=64,
        pattern=_IDENTIFIER_PATTERN,
        json_schema_extra={"pattern": _IDENTIFIER_JSON_PATTERN},
    ),
]
DonorIdentifier = Annotated[
    str,
    BeforeValidator(_validate_trimmed_text),
    Field(
        min_length=3,
        max_length=64,
        pattern=_IDENTIFIER_PATTERN,
        json_schema_extra={"pattern": _IDENTIFIER_JSON_PATTERN},
    ),
]
FactIdentifier = Annotated[
    str,
    BeforeValidator(_validate_trimmed_text),
    Field(
        min_length=5,
        max_length=64,
        pattern=_FACT_IDENTIFIER_PATTERN,
        json_schema_extra={"pattern": _FACT_IDENTIFIER_JSON_PATTERN},
    ),
]
AuditFactIdentifier = FactIdentifier | Literal["redacted.sensitive-fact-id"]
ShortText = Annotated[
    str,
    BeforeValidator(_validate_trimmed_text),
    Field(
        min_length=1,
        max_length=160,
        json_schema_extra={"pattern": _SINGLE_LINE_JSON_PATTERN},
    ),
    AfterValidator(_validate_single_line_text),
]
SubjectText = Annotated[
    str,
    BeforeValidator(_validate_trimmed_text),
    Field(
        min_length=1,
        max_length=200,
        json_schema_extra={"pattern": _SINGLE_LINE_JSON_PATTERN},
    ),
    AfterValidator(_validate_single_line_text),
]
SalutationText = Annotated[
    str,
    BeforeValidator(_validate_trimmed_text),
    Field(
        min_length=1,
        max_length=330,
        json_schema_extra={"pattern": _SINGLE_LINE_JSON_PATTERN},
    ),
    AfterValidator(_validate_single_line_text),
]
PurposeText = Annotated[
    str,
    BeforeValidator(_validate_trimmed_text),
    Field(
        min_length=20,
        max_length=600,
        json_schema_extra={"pattern": _SINGLE_LINE_JSON_PATTERN},
    ),
    AfterValidator(_validate_single_line_text),
]
DraftBodyText = Annotated[
    str,
    Field(
        min_length=1,
        max_length=6000,
        json_schema_extra={"pattern": _MULTILINE_JSON_PATTERN},
    ),
    AfterValidator(_validate_multiline_text),
]
FactText = Annotated[
    str,
    BeforeValidator(_validate_trimmed_text),
    Field(
        min_length=1,
        max_length=320,
        json_schema_extra={"pattern": _SINGLE_LINE_JSON_PATTERN},
    ),
    AfterValidator(_validate_single_line_text),
]
DiagnosticText = Annotated[
    str,
    BeforeValidator(_validate_trimmed_text),
    Field(
        min_length=1,
        max_length=320,
        json_schema_extra={"pattern": _SINGLE_LINE_JSON_PATTERN},
    ),
    AfterValidator(_validate_single_line_text),
]
PositiveMoney = Annotated[
    Decimal,
    BeforeValidator(_parse_canonical_money),
    Field(gt=Decimal("0"), max_digits=12, decimal_places=2),
    WithJsonSchema(
        {
            "type": "string",
            "pattern": _CANONICAL_MONEY_PATTERN,
            "description": "Positive decimal money value with exactly two fractional digits.",
        },
        mode="validation",
    ),
]
Multiplier = Annotated[
    Decimal,
    BeforeValidator(_parse_canonical_multiplier),
    Field(gt=Decimal("0"), le=Decimal("5"), decimal_places=2),
    WithJsonSchema(
        {
            "type": "string",
            "pattern": _CANONICAL_MULTIPLIER_PATTERN,
            "description": "Canonical multiplier from 0.01 through 5.00.",
        },
        mode="validation",
    ),
]
CanonicalDate = Annotated[
    date,
    BeforeValidator(_parse_canonical_date),
    WithJsonSchema(
        {
            "type": "string",
            "format": "date",
            "pattern": _CANONICAL_DATE_PATTERN,
            "description": "Valid Gregorian date in exact YYYY-MM-DD form.",
        },
        mode="validation",
    ),
]
CurrencyCode = Annotated[
    str,
    BeforeValidator(_validate_currency_code),
    WithJsonSchema(
        {
            "type": "string",
            "enum": list(ISO_4217_ACTIVE_CODES),
            "description": "Active ISO 4217 List One currency code as of 2026-08-01.",
        },
        mode="validation",
    ),
]
EmailAddress = Annotated[
    str,
    BeforeValidator(_validate_email_address),
    Field(
        min_length=3,
        max_length=254,
        json_schema_extra={"pattern": _EMAIL_JSON_PATTERN},
    ),
]


def contains_invalid_unicode_scalar(value: Any) -> bool:
    """Detect unpaired UTF-16 surrogate code points without recursion."""

    stack = [value]
    visited_containers: set[int] = set()
    while stack:
        current = stack.pop()
        if isinstance(current, str):
            if any(0xD800 <= ord(character) <= 0xDFFF for character in current):
                return True
            continue
        if isinstance(current, Mapping):
            container_id = id(current)
            if container_id in visited_containers:
                continue
            visited_containers.add(container_id)
            for key, item in current.items():
                stack.extend((key, item))
            continue
        if isinstance(current, Sequence) and not isinstance(current, (str, bytes, bytearray)):
            container_id = id(current)
            if container_id in visited_containers:
                continue
            visited_containers.add(container_id)
            stack.extend(current)
    return False


class ContractModel(BaseModel):
    """Base contract that rejects undeclared fields."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_default=True,
    )

    @model_validator(mode="before")
    @classmethod
    def reject_invalid_unicode_scalars(cls, value: Any) -> Any:
        if contains_invalid_unicode_scalar(value):
            raise ValueError("text must contain valid Unicode scalar values")
        return value


class Channel(StrEnum):
    EMAIL = "email"
    LETTER = "letter"


class ConsentState(StrEnum):
    GRANTED = "granted"
    DENIED = "denied"
    UNKNOWN = "unknown"


class DonorSegment(StrEnum):
    GENERAL = "general"
    MID_VALUE = "mid_value"
    MAJOR = "major"
    PRINCIPAL = "principal"
    LAPSED = "lapsed"


class FactSource(StrEnum):
    CAMPAIGN = "campaign"
    CRM = "crm"
    ORGANIZATION = "organization"


class FactCategory(StrEnum):
    DONOR_HISTORY = "donor_history"
    DONOR_PREFERENCE = "donor_preference"
    EVENT = "event"
    IMPACT = "impact"
    INCENTIVE = "incentive"
    MATCHING_GIFT = "matching_gift"
    NAMING_OPPORTUNITY = "naming_opportunity"
    PROGRAM = "program"


class PolicyDisposition(StrEnum):
    ALLOW = "allow"
    REVIEW = "review"
    BLOCK = "block"
    SUPPRESS = "suppress"


class ResultStatus(StrEnum):
    DRAFT_READY = "draft_ready"
    REVIEW_REQUIRED = "review_required"
    BLOCKED = "blocked"
    SUPPRESSED = "suppressed"
    INVALID = "invalid"
    PROVIDER_ERROR = "provider_error"
    QUALITY_REJECTED = "quality_rejected"


class ReasonCode(StrEnum):
    INVALID_DONOR_RECORD = "invalid_donor_record"
    DO_NOT_CONTACT = "do_not_contact"
    CONSENT_DENIED = "consent_denied"
    CONSENT_UNKNOWN = "consent_unknown"
    MISSING_EMAIL = "missing_email"
    MISSING_POSTAL_ADDRESS = "missing_postal_address"
    DUPLICATE_FACT_ID_ACROSS_INPUTS = "duplicate_fact_id_across_inputs"
    INSTRUCTION_LIKE_IDENTITY_FIELD = "instruction_like_identity_field"
    POLICY_CONTROL_LIKE_IDENTITY_FIELD = "policy_control_like_identity_field"
    GIVING_HISTORY_LIKE_IDENTITY_FIELD = "giving_history_like_identity_field"
    SOLICITATION_LIKE_IDENTITY_FIELD = "solicitation_like_identity_field"
    CONTACT_LIKE_IDENTITY_FIELD = "contact_like_identity_field"
    DONOR_IDENTIFIER_IN_IDENTITY_FIELD = "donor_identifier_in_identity_field"
    LAST_CONTACT_DATE_IN_FUTURE = "last_contact_date_in_future"
    LAST_GIFT_DATE_IN_FUTURE = "last_gift_date_in_future"
    GIVING_CURRENCY_MISMATCH = "giving_currency_mismatch"
    MISSING_ASK_BASIS = "missing_ask_basis"
    CONTACT_FREQUENCY_LIMIT = "contact_frequency_limit"
    RELATIONSHIP_MANAGED_SEGMENT = "relationship_managed_segment"
    HIGH_VALUE_ASK = "high_value_ask"
    INSTRUCTION_LIKE_FACT_EXCLUDED = "instruction_like_fact_excluded"
    SOLICITATION_LIKE_FACT_EXCLUDED = "solicitation_like_fact_excluded"
    MONETARY_FACT_EXCLUDED = "monetary_fact_excluded"
    CONTACT_LIKE_FACT_EXCLUDED = "contact_like_fact_excluded"
    DONOR_IDENTIFIER_FACT_EXCLUDED = "donor_identifier_fact_excluded"
    POLICY_CONTROL_LIKE_FACT_EXCLUDED = "policy_control_like_fact_excluded"
    GIVING_HISTORY_FACT_EXCLUDED = "giving_history_fact_excluded"
    UNVERIFIED_PROVIDER_REQUIRES_REVIEW = "unverified_provider_requires_review"
    DRAFT_REQUEST_INVALID = "draft_request_invalid"
    PROVIDER_GENERATION_FAILED = "provider_generation_failed"
    DRAFT_FAILED_QUALITY_GATE = "draft_failed_quality_gate"


class QualityCode(StrEnum):
    DUPLICATE_FACT_REFERENCE = "duplicate_fact_reference"
    UNAPPROVED_FACT_REFERENCE = "unapproved_fact_reference"
    UNDECLARED_FACT_USAGE = "undeclared_fact_usage"
    UNUSED_FACT_REFERENCE = "unused_fact_reference"
    BODY_STRUCTURE_INVALID = "body_structure_invalid"
    SALUTATION_MISMATCH = "salutation_mismatch"
    MISSING_SUBJECT = "missing_subject"
    UNEXPECTED_SUBJECT = "unexpected_subject"
    CAMPAIGN_PURPOSE_MISMATCH = "campaign_purpose_mismatch"
    MISSING_SENDER = "missing_sender"
    SENDER_SIGNOFF_MISMATCH = "sender_signoff_mismatch"
    HTML_NOT_ALLOWED = "html_not_allowed"
    MISSING_CALL_TO_ACTION_URL = "missing_call_to_action_url"
    CALL_TO_ACTION_MISMATCH = "call_to_action_mismatch"
    UNAPPROVED_URL = "unapproved_url"
    UNAPPROVED_CONTACT_DETAIL = "unapproved_contact_detail"
    UNGROUNDED_NUMBER = "ungrounded_number"
    UNAUTHORIZED_ASK_AMOUNT = "unauthorized_ask_amount"
    UNAUTHORIZED_ASK_LANGUAGE = "unauthorized_ask_language"
    INSTRUCTION_LIKE_OUTPUT = "instruction_like_output"
    UNAUTHORIZED_POLICY_CONTROL = "unauthorized_policy_control"
    ASK_COPY_MISMATCH = "ask_copy_mismatch"
    ASK_AMOUNT_MISMATCH = "ask_amount_mismatch"
    CAMPAIGN_PROHIBITED_PHRASE = "campaign_prohibited_phrase"
    MANIPULATIVE_PRESSURE = "manipulative_pressure"
    UNSUPPORTED_MATCHING_GIFT = "unsupported_matching_gift"
    UNSUPPORTED_NAMING_OPPORTUNITY = "unsupported_naming_opportunity"
    UNSUPPORTED_INCENTIVE = "unsupported_incentive"
    UNSUPPORTED_EVENT = "unsupported_event"
    UNSUPPORTED_IMPACT = "unsupported_impact"


class PostalAddress(ContractModel):
    line_1: ShortText
    line_2: ShortText | None = None
    city: ShortText
    region: ShortText
    postal_code: ShortText
    country_code: Annotated[
        str,
        Field(
            pattern=_COUNTRY_CODE_PATTERN,
            json_schema_extra={"pattern": _COUNTRY_CODE_JSON_PATTERN},
        ),
    ]


class GivingHistory(ContractModel):
    model_config = ConfigDict(
        json_schema_extra={
            "oneOf": [
                {
                    "properties": {
                        "last_gift_amount": {"type": "null"},
                        "last_gift_date": {"type": "null"},
                    }
                },
                {
                    "properties": {
                        "last_gift_amount": {"not": {"type": "null"}},
                        "last_gift_date": {"type": "string", "format": "date"},
                    }
                },
            ]
        }
    )

    currency: CurrencyCode
    last_gift_amount: PositiveMoney | None
    largest_gift_amount: PositiveMoney | None
    lifetime_value: PositiveMoney | None
    last_gift_date: CanonicalDate | None

    @model_validator(mode="after")
    def validate_consistency(self) -> Self:
        if (self.last_gift_amount is None) != (self.last_gift_date is None):
            raise ValueError("last_gift_amount and last_gift_date must be supplied together")
        if (
            self.last_gift_amount is not None
            and self.largest_gift_amount is not None
            and self.largest_gift_amount < self.last_gift_amount
        ):
            raise ValueError("largest_gift_amount cannot be below last_gift_amount")
        if (
            self.largest_gift_amount is not None
            and self.lifetime_value is not None
            and self.lifetime_value < self.largest_gift_amount
        ):
            raise ValueError("lifetime_value cannot be below largest_gift_amount")
        if (
            self.last_gift_amount is not None
            and self.lifetime_value is not None
            and self.lifetime_value < self.last_gift_amount
        ):
            raise ValueError("lifetime_value cannot be below last_gift_amount")
        return self


def _approved_fact_json_schema_extra(schema: dict[str, Any]) -> None:
    source_namespaces = {
        FactSource.CAMPAIGN: r"^campaign\.[a-z0-9][a-z0-9-]*$(?![\s\S])",
        FactSource.ORGANIZATION: r"^organization\.[a-z0-9][a-z0-9-]*$(?![\s\S])",
        FactSource.CRM: r"^(?:crm|donor)\.[a-z0-9][a-z0-9-]*$(?![\s\S])",
    }
    schema["allOf"] = [
        {
            "if": {
                "properties": {"source": {"const": source.value}},
                "required": ["source"],
            },
            "then": {"properties": {"fact_id": {"pattern": pattern}}},
        }
        for source, pattern in source_namespaces.items()
    ]


class ApprovedFact(ContractModel):
    model_config = ConfigDict(json_schema_extra=_approved_fact_json_schema_extra)

    fact_id: FactIdentifier
    text: FactText
    source: FactSource
    category: FactCategory
    approved_for_outreach: bool = Field(strict=True)

    @model_validator(mode="after")
    def validate_source_namespace(self) -> Self:
        allowed_prefixes = {
            FactSource.CAMPAIGN: ("campaign.",),
            FactSource.ORGANIZATION: ("organization.",),
            FactSource.CRM: ("crm.", "donor."),
        }
        if not self.fact_id.startswith(allowed_prefixes[self.source]):
            raise ValueError("fact_id namespace must match fact source")
        return self


def _set_fact_source_schema(schema: dict[str, Any], source_schema: dict[str, Any]) -> None:
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return
    facts = properties.get("facts")
    if not isinstance(facts, dict):
        return
    items = facts.get("items")
    if not isinstance(items, dict):
        return
    items["properties"] = {"source": source_schema}


def _donor_json_schema_extra(schema: dict[str, Any]) -> None:
    _set_fact_source_schema(schema, {"const": FactSource.CRM.value})


def _campaign_json_schema_extra(schema: dict[str, Any]) -> None:
    _set_fact_source_schema(
        schema,
        {
            "enum": [
                FactSource.CAMPAIGN.value,
                FactSource.ORGANIZATION.value,
            ]
        },
    )


class DonorRecord(ContractModel):
    model_config = ConfigDict(json_schema_extra=_donor_json_schema_extra)

    donor_id: DonorIdentifier
    first_name: ShortText
    last_name: ShortText | None
    title: ShortText | None
    preferred_channel: Channel
    channel_consent: ConsentState
    do_not_contact: bool = Field(strict=True)
    email: EmailAddress | None
    postal_address: PostalAddress | None
    segment: DonorSegment
    giving: GivingHistory
    last_contact_date: CanonicalDate | None
    facts: Annotated[tuple[ApprovedFact, ...], Field(max_length=25)]

    @model_validator(mode="after")
    def validate_fact_ids(self) -> Self:
        fact_ids = [fact.fact_id for fact in self.facts]
        if len(fact_ids) != len(set(fact_ids)):
            raise ValueError("donor fact_id values must be unique")
        if any(fact.source != FactSource.CRM for fact in self.facts):
            raise ValueError("donor facts must use the crm source")
        return self


class CallToAction(ContractModel):
    label: ShortText
    url: Annotated[
        str,
        Field(
            max_length=2048,
            json_schema_extra={
                "pattern": _CTA_URL_JSON_PATTERN,
            },
        ),
    ]

    @field_validator("url", mode="before")
    @classmethod
    def require_https_url(cls, value: Any) -> Any:
        if not isinstance(value, str) or value != value.strip():
            raise ValueError("call-to-action URL must be a trimmed string")
        if not value.startswith("https://"):
            raise ValueError("call-to-action URL scheme must be canonical lowercase https")
        if (
            any(
                character.isspace()
                or is_unsafe_text_character(character)
                or character == "\\"
                or character == "%"
                for character in value
            )
            or not value.isascii()
        ):
            raise ValueError("call-to-action URL contains unsafe characters")
        try:
            parsed = urlsplit(value)
            hostname = parsed.hostname
            port = parsed.port
        except ValueError as error:
            raise ValueError("call-to-action URL authority is invalid") from error
        labels = hostname.split(".") if hostname is not None else []
        if (
            parsed.scheme != "https"
            or hostname is None
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or port is not None
            or parsed.netloc.casefold() != hostname.casefold()
            or not hostname.isascii()
            or len(hostname) > 253
            or len(labels) < 2
            or not any(character.isalpha() for character in labels[-1])
            or any(_HOST_LABEL_PATTERN.fullmatch(label) is None for label in labels)
            or re.fullmatch(r"/[A-Za-z0-9._~!$&'()*+,;=:@/-]*|", parsed.path) is None
        ):
            raise ValueError(
                "call-to-action URL must use canonical HTTPS, a valid ASCII DNS host, no port "
                "or credentials, and no query, fragment, or unsafe path characters"
            )
        return value


class Sender(ContractModel):
    name: ShortText
    role: ShortText


class NoAskPolicy(ContractModel):
    strategy: Literal["none"]
    currency: CurrencyCode


class MultiplierAskPolicy(ContractModel):
    strategy: Literal["last_gift_multiplier"]
    currency: CurrencyCode
    multiplier: Multiplier
    rounding_increment: PositiveMoney
    minimum: PositiveMoney
    maximum: PositiveMoney

    @model_validator(mode="after")
    def validate_bounds(self) -> Self:
        if self.minimum > self.maximum:
            raise ValueError("ask-policy minimum cannot exceed maximum")
        return self


AskPolicy = Annotated[
    NoAskPolicy | MultiplierAskPolicy,
    Field(discriminator="strategy"),
]


class ReviewPolicy(ContractModel):
    segments: Annotated[
        tuple[DonorSegment, ...],
        Field(max_length=5, json_schema_extra={"uniqueItems": True}),
    ]
    ask_amount_at_or_above: PositiveMoney

    @model_validator(mode="after")
    def validate_unique_segments(self) -> Self:
        if len(self.segments) != len(set(self.segments)):
            raise ValueError("review-policy segments must be unique")
        return self


class CampaignBrief(ContractModel):
    model_config = ConfigDict(json_schema_extra=_campaign_json_schema_extra)

    campaign_id: Identifier
    organization_name: ShortText
    campaign_name: ShortText
    purpose: PurposeText
    tone: Literal["warm", "warm-professional", "formal"]
    sender: Sender
    call_to_action: CallToAction
    as_of_date: CanonicalDate
    minimum_days_between_contacts: Annotated[int, Field(ge=0, le=365, strict=True)]
    ask_policy: AskPolicy
    review_policy: ReviewPolicy
    facts: Annotated[tuple[ApprovedFact, ...], Field(max_length=50)]
    prohibited_phrases: Annotated[tuple[ShortText, ...], Field(max_length=50)]

    @model_validator(mode="after")
    def validate_fact_ids(self) -> Self:
        fact_ids = [fact.fact_id for fact in self.facts]
        if len(fact_ids) != len(set(fact_ids)):
            raise ValueError("campaign fact_id values must be unique")
        if any(fact.source == FactSource.CRM for fact in self.facts):
            raise ValueError("campaign facts must use campaign or organization source")
        return self


class Money(ContractModel):
    amount: PositiveMoney
    currency: CurrencyCode


class PolicyDecision(ContractModel):
    disposition: PolicyDisposition
    generation_allowed: bool
    reason_codes: list[ReasonCode]
    ask: Money | None
    eligible_facts: tuple[ApprovedFact, ...]
    excluded_fact_ids: list[AuditFactIdentifier]


class DraftRequest(ContractModel):
    channel: Channel
    salutation: SalutationText
    organization_name: ShortText
    campaign_name: ShortText
    purpose: PurposeText
    tone: Literal["warm", "warm-professional", "formal"]
    sender: Sender
    call_to_action: CallToAction
    ask: Money | None
    facts: tuple[ApprovedFact, ...]


class DraftCandidate(ContractModel):
    subject_line: SubjectText | None
    salutation: SalutationText
    body: DraftBodyText
    fact_ids_used: Annotated[list[FactIdentifier], Field(max_length=25)]


class DraftArtifact(ContractModel):
    subject_line: SubjectText | None
    body: DraftBodyText
    ask: Money | None
    fact_ids_used: Annotated[
        tuple[FactIdentifier, ...],
        Field(max_length=25, json_schema_extra={"uniqueItems": True}),
    ]

    @model_validator(mode="after")
    def validate_unique_fact_ids(self) -> Self:
        if len(self.fact_ids_used) != len(set(self.fact_ids_used)):
            raise ValueError("draft fact_ids_used values must be unique")
        return self


class ValidationIssue(ContractModel):
    field: DiagnosticText
    code: Identifier
    message: DiagnosticText


class QualityIssue(ContractModel):
    code: QualityCode
    message: ShortText


class AuditMetadata(ContractModel):
    model_config = ConfigDict(
        json_schema_extra={
            "allOf": [
                {
                    "if": {
                        "properties": {"provider_called": {"const": True}},
                        "required": ["provider_called"],
                    },
                    "then": {"properties": {"provider_name": {"type": "string"}}},
                    "else": {"properties": {"provider_name": {"type": "null"}}},
                }
            ]
        }
    )

    policy_version: ShortText
    evaluated_on: CanonicalDate
    input_fingerprint: Annotated[
        str,
        Field(
            pattern=_FINGERPRINT_PATTERN,
            json_schema_extra={"pattern": _FINGERPRINT_JSON_PATTERN},
        ),
    ]
    provider_name: ShortText | None
    provider_called: Annotated[bool, Field(strict=True)]
    excluded_fact_ids: Annotated[
        list[AuditFactIdentifier],
        Field(max_length=75, json_schema_extra={"uniqueItems": True}),
    ]

    @model_validator(mode="after")
    def validate_provider_metadata(self) -> Self:
        if self.provider_called != (self.provider_name is not None):
            raise ValueError("provider_name must be present exactly when provider_called is true")
        if len(self.excluded_fact_ids) != len(set(self.excluded_fact_ids)):
            raise ValueError("excluded_fact_ids values must be unique")
        return self


def _result_schema_branch(
    status: ResultStatus,
    *,
    has_draft: bool,
    provider_called: bool,
    review_required: bool,
    has_validation_issues: bool = False,
    has_quality_issues: bool = False,
) -> dict[str, Any]:
    return {
        "properties": {
            "status": {"const": status.value},
            "draft": ({"not": {"type": "null"}} if has_draft else {"type": "null"}),
            "review_required": {"const": review_required},
            "channel": (
                {"type": "null"}
                if status == ResultStatus.INVALID
                else {"enum": [Channel.EMAIL.value, Channel.LETTER.value]}
            ),
            "donor_id": ({} if status == ResultStatus.INVALID else {"type": "string"}),
            "reason_codes": (
                {"maxItems": 0} if status == ResultStatus.DRAFT_READY else {"minItems": 1}
            ),
            "validation_issues": ({"minItems": 1} if has_validation_issues else {"maxItems": 0}),
            "quality_issues": ({"minItems": 1} if has_quality_issues else {"maxItems": 0}),
            "audit": {
                "properties": {"provider_called": {"const": provider_called}},
                "required": ["provider_called"],
            },
        },
        "required": ["status"],
    }


_RESULT_SUBJECT_SCHEMA_RULES: list[dict[str, Any]] = [
    {
        "if": {
            "properties": {
                "channel": {"const": Channel.EMAIL.value},
                "draft": {"not": {"type": "null"}},
            },
            "required": ["channel", "draft"],
        },
        "then": {"properties": {"draft": {"properties": {"subject_line": {"type": "string"}}}}},
    },
    {
        "if": {
            "properties": {
                "channel": {"const": Channel.LETTER.value},
                "draft": {"not": {"type": "null"}},
            },
            "required": ["channel", "draft"],
        },
        "then": {"properties": {"draft": {"properties": {"subject_line": {"type": "null"}}}}},
    },
]


class OutreachResult(ContractModel):
    model_config = ConfigDict(
        json_schema_extra=cast(
            Any,
            {
                "oneOf": [
                    _result_schema_branch(
                        ResultStatus.INVALID,
                        has_draft=False,
                        provider_called=False,
                        review_required=True,
                        has_validation_issues=True,
                    ),
                    _result_schema_branch(
                        ResultStatus.BLOCKED,
                        has_draft=False,
                        provider_called=False,
                        review_required=True,
                    ),
                    _result_schema_branch(
                        ResultStatus.SUPPRESSED,
                        has_draft=False,
                        provider_called=False,
                        review_required=False,
                    ),
                    _result_schema_branch(
                        ResultStatus.PROVIDER_ERROR,
                        has_draft=False,
                        provider_called=True,
                        review_required=True,
                    ),
                    _result_schema_branch(
                        ResultStatus.QUALITY_REJECTED,
                        has_draft=False,
                        provider_called=True,
                        review_required=True,
                        has_quality_issues=True,
                    ),
                    _result_schema_branch(
                        ResultStatus.REVIEW_REQUIRED,
                        has_draft=True,
                        provider_called=True,
                        review_required=True,
                    ),
                    _result_schema_branch(
                        ResultStatus.DRAFT_READY,
                        has_draft=True,
                        provider_called=True,
                        review_required=False,
                    ),
                ],
                "allOf": _RESULT_SUBJECT_SCHEMA_RULES,
            },
        )
    )

    record_index: Annotated[int, Field(ge=1, strict=True)]
    donor_id: Identifier | None
    campaign_id: Identifier
    channel: Channel | None
    status: ResultStatus
    review_required: Annotated[bool, Field(strict=True)]
    reason_codes: Annotated[
        list[ReasonCode],
        Field(max_length=25, json_schema_extra={"uniqueItems": True}),
    ]
    validation_issues: Annotated[list[ValidationIssue], Field(max_length=25)]
    quality_issues: list[QualityIssue]
    draft: DraftArtifact | None
    audit: AuditMetadata

    @model_validator(mode="after")
    def validate_state_envelope(self) -> Self:
        provider_statuses = {
            ResultStatus.PROVIDER_ERROR,
            ResultStatus.QUALITY_REJECTED,
            ResultStatus.REVIEW_REQUIRED,
            ResultStatus.DRAFT_READY,
        }
        draft_statuses = {ResultStatus.REVIEW_REQUIRED, ResultStatus.DRAFT_READY}
        review_statuses = {
            ResultStatus.INVALID,
            ResultStatus.BLOCKED,
            ResultStatus.PROVIDER_ERROR,
            ResultStatus.QUALITY_REJECTED,
            ResultStatus.REVIEW_REQUIRED,
        }

        if (self.draft is not None) != (self.status in draft_statuses):
            raise ValueError("draft presence is inconsistent with result status")
        if self.audit.provider_called != (self.status in provider_statuses):
            raise ValueError("provider_called is inconsistent with result status")
        if self.review_required != (self.status in review_statuses):
            raise ValueError("review_required is inconsistent with result status")
        if (self.channel is None) != (self.status == ResultStatus.INVALID):
            raise ValueError("channel presence is inconsistent with result status")
        if self.draft is not None:
            if self.channel == Channel.EMAIL and self.draft.subject_line is None:
                raise ValueError("email draft results require a subject_line")
            if self.channel == Channel.LETTER and self.draft.subject_line is not None:
                raise ValueError("letter draft results require a null subject_line")
        if self.status != ResultStatus.INVALID and self.donor_id is None:
            raise ValueError("non-invalid results require a donor_id")
        if (bool(self.validation_issues)) != (self.status == ResultStatus.INVALID):
            raise ValueError("validation_issues are inconsistent with result status")
        if (bool(self.quality_issues)) != (self.status == ResultStatus.QUALITY_REJECTED):
            raise ValueError("quality_issues are inconsistent with result status")
        if self.status == ResultStatus.DRAFT_READY and self.reason_codes:
            raise ValueError("draft_ready must not contain reason codes")
        if self.status != ResultStatus.DRAFT_READY and not self.reason_codes:
            raise ValueError("non-ready results require at least one reason code")
        if len(self.reason_codes) != len(set(self.reason_codes)):
            raise ValueError("reason_codes values must be unique")
        quality_codes = [issue.code for issue in self.quality_issues]
        if len(quality_codes) != len(set(quality_codes)):
            raise ValueError("quality issue codes must be unique")
        return self
