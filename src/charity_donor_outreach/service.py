"""Orchestration with fail-closed policy and per-record isolation."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import math
import re
import unicodedata
from collections.abc import Callable, Hashable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from functools import cache
from html import unescape
from html.entities import codepoint2name, html5
from typing import Any, TypeVar, cast
from urllib.parse import unquote, urlsplit

from pydantic import ValidationError

from ._e164 import E164_ASSIGNED_CALLING_CODE_SET, E164_POSSIBLE_NATIONAL_LENGTHS
from ._iana_tlds import IANA_ROOT_ZONE_TLD_SET
from ._iso4217 import ISO_4217_ACTIVE_CODE_SET
from .guard import DraftGuard
from .models import (
    AuditMetadata,
    CampaignBrief,
    DonorRecord,
    DraftArtifact,
    DraftCandidate,
    DraftRequest,
    OutreachResult,
    PolicyDecision,
    PolicyDisposition,
    ReasonCode,
    ResultStatus,
    ValidationIssue,
    is_unsafe_text_character,
)
from .policy import (
    _SECURITY_SENTENCE_BREAK,
    POLICY_VERSION,
    contains_contact_like_text,
    contains_donor_contact_value,
    contains_donor_identifier,
    contains_giving_history_field_like_text,
    contains_giving_history_like_text,
    contains_instruction_like_text,
    contains_policy_control_like_text,
    contains_solicitation_language,
    donor_contact_literals,
    evaluate_policy,
    money_expressions,
    security_view,
    security_views,
)
from .providers import DraftProvider, TemplateProvider

_SAFE_DONOR_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,63}$")
_SAFE_PROVIDER_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
_MAX_DIRECT_INPUT_DEPTH = 64
_MAX_DIRECT_INPUT_NODES = 10_000
_MAX_DIRECT_CONTAINER_ITEMS = 1_000
_MAX_DIRECT_INPUT_SCALAR_UNITS = 1_048_576
_MAX_DIRECT_FACTS = 25
_MAX_VALIDATION_ISSUES = 25
_MAX_DIRECT_INTEGER_BITS = (10**128).bit_length()
_MAX_DIRECT_DECIMAL_STORAGE_BYTES = 4_096
_DECLARED_DONOR_LOCATION_FIELDS = frozenset(
    {
        "donor_id",
        "first_name",
        "last_name",
        "title",
        "preferred_channel",
        "channel_consent",
        "do_not_contact",
        "email",
        "postal_address",
        "segment",
        "giving",
        "last_contact_date",
        "facts",
        "line_1",
        "line_2",
        "city",
        "region",
        "postal_code",
        "country_code",
        "currency",
        "last_gift_amount",
        "largest_gift_amount",
        "lifetime_value",
        "last_gift_date",
        "fact_id",
        "text",
        "source",
        "category",
        "approved_for_outreach",
    }
)


class CampaignConfigurationError(ValueError):
    """Raised when trusted campaign configuration resembles model instructions."""


class ProviderConfigurationError(ValueError):
    """Raised when the provider boundary lacks safe, stable metadata."""


class _InputSnapshotError(ValueError):
    """Represent a bounded direct-input rejection with a stable diagnostic code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


_BOUNDARY_WINDOW = 192
_BOUNDARY_BATCH_SIZE = 128
_BOUNDARY_BATCH_SENTINEL = "\n" + ("qzboundarybreak" * 12) + "\n"
_BOUNDARY_CATEGORY_WINDOWS = {
    "contact": 128,
    "instruction": 128,
    "policy": 64,
    "giving": 128,
    "solicitation": 64,
    "money": 128,
}
_MAX_LITERAL_CLOSURE_STATES = 50_000
_MAX_COMPONENT_GRAMMAR_STATES = 20_000
_MAX_SPLIT_ESCAPE_LENGTH = 34
_ALL_BOUNDARY_CATEGORIES = frozenset(
    {"contact", "instruction", "policy", "giving", "solicitation", "money"}
)
_CTA_CONTROL_BOUNDARY_CATEGORIES = frozenset({"contact", "instruction", "policy", "giving"})
_MONEY_BOUNDARY_CATEGORY = frozenset({"money"})
_BOUNDARY_ENCODING_HINT = re.compile(
    r"(?:%u?[0-9a-f]{2,8}|&#(?:x[0-9a-f]+|[0-9]+);|\\(?:x[0-9a-f]{2}|u[0-9a-f]{4}|"
    r"U[0-9a-f]{8}|[0-7]{1,3}))",
    re.IGNORECASE,
)
_BOUNDARY_INSTRUCTION_HINT = re.compile(
    r"\b(?:agent|assistant|bypass|change|developer|directions?|disclose|disregard|draft|"
    r"email|exfiltrat(?:e|ion)|forget|format|generate|guardrails?|hidden|ignore|include|"
    r"instructions?|jailbreak|message|model|omit|output|override|please|print|prompt|"
    r"publish|replace|return|reveal|rewrite|rules?|send|submit|system|treat|use|write)\b",
    re.IGNORECASE,
)
_BOUNDARY_POLICY_HINT = re.compile(
    r"\b(?:ask|channel|consent|contact|contactable|dnc|guard|last|mail|marketing|maximum|"
    r"minimum|multiplier|opt|permission|policy|postal|preferred|review|rounding|segment|"
    r"status|suppression|unsubscribe)\b",
    re.IGNORECASE,
)
_BOUNDARY_GIVING_HINT = re.compile(
    r"\b(?:amount|annual|contribut(?:e|ed|ion|ions)|currency|date|donat(?:e|ed|ion|ions)|"
    r"donor|frequency|gave|gift|gifted|gifts|giving|history|household|lifetime|pledge|"
    r"pledged|supporter|value|years)\b",
    re.IGNORECASE,
)
_BOUNDARY_SOLICITATION_HINT = re.compile(
    r"\b(?:ask|chip|contribut(?:e|ed|ing|ion|ions)|donat(?:e|ed|ing|ion|ions)|donor|"
    r"fund|funding|fundrais(?:e|ed|ing)|generosity|gift|gifts|give|giving|help|join|"
    r"pitch|pledge|pledged|pledging|support|supporting)\b",
    re.IGNORECASE,
)
_BOUNDARY_CURRENCY_WORD_OR_SYMBOL_HINT = re.compile(
    r"(?:[$\u00a2-\u00a5\u058f\u060b\u09f2\u09f3\u09fb\u0af1\u0bf9\u0e3f\u17db"
    r"\u20a0-\u20c0\ua838\ufdfc\ufe69\uff04\uffe0\uffe1\uffe5\uffe6]|\b(?:"
    r"dollars?|euros?|pounds?|yen|yuan|rupees?|pesos?|francs?|"
    r"shillings?|dinars?|dirhams?|riyals?|rials?|rubles?|roubles?|nairas?|cedis?)\b)",
    re.IGNORECASE,
)
_BOUNDARY_THREE_LETTER_WORD = re.compile(r"(?<![A-Za-z])[A-Za-z]{3}(?![A-Za-z])")
_BOUNDARY_NUMBER_HINT = re.compile(
    r"(?:\d|\b(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
    r"thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|"
    r"forty|fifty|sixty|seventy|eighty|ninety|hundred|thousand|million|billion)\b)",
    re.IGNORECASE,
)
_COMPONENT_NUMBER_WORDS = frozenset(
    {
        "zero",
        "one",
        "two",
        "three",
        "four",
        "five",
        "six",
        "seven",
        "eight",
        "nine",
        "ten",
        "eleven",
        "twelve",
        "thirteen",
        "fourteen",
        "fifteen",
        "sixteen",
        "seventeen",
        "eighteen",
        "nineteen",
        "twenty",
        "thirty",
        "forty",
        "fifty",
        "sixty",
        "seventy",
        "eighty",
        "ninety",
        "hundred",
        "thousand",
        "million",
        "billion",
    }
)
_COMPONENT_INSTRUCTION_GROUPS = (
    (
        ("ignore", "disregard", "forget", "override", "replace"),
        (
            "instruction",
            "instructions",
            "direction",
            "directions",
            "rule",
            "rules",
            "prompt",
            "prompts",
            "message",
            "messages",
        ),
    ),
    (("system",), ("prompt", "instructions", "rules")),
    (("developer",), ("message", "prompt", "instructions")),
    (
        ("assistant", "model", "agent", "system"),
        ("prompt", "instruction", "instructions", "direction", "directions", "rule", "rules"),
    ),
    (
        ("reveal", "print", "output", "return", "disclose"),
        ("hidden", "system", "developer", "prompt", "instruction", "instructions", "rules"),
    ),
    (("bypass",), ("review", "policy", "guardrail", "guardrails")),
    (
        ("send", "email", "deliver", "publish", "submit"),
        ("now", "immediately", "withoutreview", "withoutapproval"),
    ),
    (
        (
            "write",
            "generate",
            "include",
            "omit",
            "use",
            "change",
            "follow",
            "render",
            "rewrite",
            "format",
            "draft",
            "treat",
        ),
        (
            "email",
            "draft",
            "text",
            "copy",
            "verbatim",
            "allcaps",
            "campaignpurpose",
            "salutation",
            "askamount",
            "direction",
            "directions",
            "instruction",
            "instructions",
            "json",
            "prompt",
            "fact",
            "message",
            "response",
        ),
    ),
)
_COMPONENT_MINIMUM_PLAIN_RECONSTRUCTION_COMPONENTS = {"campaignpurpose": 3}
_COMPONENT_HIGH_RISK_TARGETS = (
    "return",
    "output",
    "jailbreak",
    "exfiltrate",
    "exfiltration",
    "donotcontact",
    "donotemail",
    "donotmail",
    "optout",
    "unsubscribe",
    "emailpermission",
    "marketingpermission",
    "contactpermission",
    "contactable",
    "emailsuppression",
    "channelconsent",
    "preferredchannel",
    "lastcontactdate",
    "donorsegment",
    "postaladdress",
    "minimumdaysbetweencontacts",
    "askpolicy",
    "reviewpolicy",
    "askamount",
    "roundingincrement",
    "minimumask",
    "maximumask",
    "askmultiplier",
    "reviewrequired",
    "policyreview",
    "policystatus",
    "guardstatus",
    "guardflag",
    "guardsetting",
    "policyflag",
    "policysetting",
    "overridestatus",
    "approvedbycompliance",
    "approvedbyguard",
    "approvedbypolicy",
    "clearedcompliance",
    "clearedguard",
    "clearedpolicy",
    "passedcompliance",
    "passedguard",
    "passedpolicy",
    "lastgift",
    "lastgiftamount",
    "lastgiftdate",
    "largestgift",
    "largestgiftamount",
    "previousgift",
    "priorgift",
    "giftamount",
    "giftcurrency",
    "giftdate",
    "giftfrequency",
    "givinghistory",
    "donationhistory",
    "lifetimevalue",
    "householdgiving",
    "annualgiving",
    "consecutivegivingyears",
    "yourgift",
    "yourdonation",
    "yourcontribution",
    "makeagift",
    "makeadonation",
    "makeacontribution",
    "donatetoday",
    "donatenow",
    "givetoday",
    "givenow",
    "contributetoday",
    "contributenow",
    "pledgetoday",
    "pledgenow",
)
_COMPONENT_SOLICITATION_WORDS = (
    "donate",
    "donates",
    "donating",
    "contribute",
    "contributes",
    "contributing",
    "pledge",
    "pledges",
    "pledging",
    "give",
    "help",
    "support",
    "fund",
    "join",
    "chipin",
    "pitchin",
    "donationwelcome",
    "donationswelcome",
    "donationarewelcome",
    "donationsarewelcome",
    "contributionwelcome",
    "contributionswelcome",
    "contributionarewelcome",
    "contributionsarewelcome",
    "giftwelcome",
    "giftswelcome",
    "giftarewelcome",
    "giftsarewelcome",
)
_COMPONENT_GIVING_VERBS = (
    "gave",
    "gifted",
    "donated",
    "contributed",
    "pledged",
)
_COMPONENT_CURRENCY_NAMES = (
    "dollar",
    "dollars",
    "euro",
    "euros",
    "pound",
    "pounds",
    "yen",
    "yuan",
    "rupee",
    "rupees",
    "peso",
    "pesos",
    "franc",
    "francs",
    "shilling",
    "shillings",
    "dinar",
    "dinars",
    "dirham",
    "dirhams",
    "riyal",
    "riyals",
    "rial",
    "rials",
    "ruble",
    "rubles",
    "rouble",
    "roubles",
    "naira",
    "nairas",
    "cedi",
    "cedis",
)
_BOUNDARY_CONTACT_HINT = re.compile(
    r"(?:[@+]|\b(?:address|at|box|browse|call|colon|contact|dial|dot|e-mail|email|"
    r"extension|phone|point|period|reach|sms|street|tel|telephone|text|url|visit|"
    r"website|write\s+to|www)\b|[A-Za-z0-9]\.[A-Za-z0-9]|^\s*\.[A-Za-z0-9])",
    re.IGNORECASE,
)
_CONTACT_EMAIL_CUE = re.compile(
    r"\b(?:contact|e-mail|email|reach|write\s+to)\b",
    re.IGNORECASE,
)
_CONTACT_WEB_CUE = re.compile(
    r"\b(?:browse|go\s+to|url|visit|web\s+site|website)\b",
    re.IGNORECASE,
)
_CONTACT_STANDALONE_CUE = re.compile(
    r"\s*(?:browse|contact|e-mail|email|go\s+to|reach|url|visit|web\s+site|website|"
    r"write\s+to)\s*[:.!?;]?[\s]*",
    re.IGNORECASE,
)
_CONTACT_FRAGMENT_STRONG_AT = re.compile(
    r"[\[({<]\s*(?:at|@)\s*[\])}>]",
    re.IGNORECASE,
)
_CONTACT_FRAGMENT_STRONG_DOT = re.compile(
    r"[\[({<]\s*(?:dot|period|point|\.)\s*[\])}>]",
    re.IGNORECASE,
)
_CONTACT_FRAGMENT_STRONG_COLON = re.compile(
    r"[\[({<]\s*(?:colon|:)\s*[\])}>]",
    re.IGNORECASE,
)
_CONTACT_FRAGMENT_TOKEN = re.compile(r"@|[.:]|[^\W_](?:[^\W_]|-)*", re.UNICODE)
_SPLIT_SECURITY_ESCAPE = re.compile(
    r"(?:%u[0-9a-f]{4}|(?:%[0-9a-f]{2}){1,4}|&#(?:[0-9]{1,7}|x[0-9a-f]{1,6});|"
    r"&[a-z][a-z0-9]{1,31};|\\x[0-9a-f]{2}|\\u[0-9a-f]{4}|"
    r"\\U[0-9a-f]{8}|\\[0-7]{3})",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class _PreparedBoundaryComponent:
    head: str
    tail: str


def _boundary_pair_views(first: str, second: str) -> tuple[str, str]:
    """Return space and compact views around one bounded serialization boundary."""

    first_tail = first[-_BOUNDARY_WINDOW:]
    second_head = second[:_BOUNDARY_WINDOW]
    compact_first = first_tail.rstrip(" \t\r\n.,;:!?")
    compact_second = second_head.lstrip(" \t\r\n.,;:!?")
    return f"{first_tail} {second_head}", f"{compact_first}{compact_second}"


def _pairwise_boundary_views(values: Sequence[str]) -> tuple[str, ...]:
    """Join every indexed field pair in both orders across bounded boundary windows."""

    bounded_values = tuple(value for value in values if value)
    views: list[str] = []
    for left_index, left in enumerate(bounded_values):
        for right in bounded_values[left_index + 1 :]:
            for first, second in ((left, right), (right, left)):
                views.extend(_boundary_pair_views(first, second))
    return tuple(dict.fromkeys(views))


def _contact_boundary_hint(value: str) -> bool:
    if _BOUNDARY_CONTACT_HINT.search(value) or _BOUNDARY_ENCODING_HINT.search(value):
        return True
    numeric_positions = [index for index, character in enumerate(value) if character.isnumeric()]
    required_numeric_count = 3 if "+" in value else 7
    if len(numeric_positions) >= required_numeric_count:
        for start in range(len(numeric_positions) - required_numeric_count + 1):
            if (
                numeric_positions[start + required_numeric_count - 1] - numeric_positions[start]
                <= 48
            ):
                return True
    number_word_matches = tuple(_BOUNDARY_NUMBER_HINT.finditer(value))
    if len(number_word_matches) >= 7:
        return any(
            number_word_matches[start + 6].end() - number_word_matches[start].start() <= 96
            for start in range(len(number_word_matches) - 6)
        )
    return False


def _currency_boundary_hint(value: str) -> bool:
    if _BOUNDARY_CURRENCY_WORD_OR_SYMBOL_HINT.search(value):
        return True
    return any(
        match.group(0).upper() in ISO_4217_ACTIVE_CODE_SET
        for match in _BOUNDARY_THREE_LETTER_WORD.finditer(value)
    )


def _prepare_boundary_components(
    values: Sequence[str],
) -> tuple[_PreparedBoundaryComponent, ...]:
    prepared: list[_PreparedBoundaryComponent] = []
    for value in values:
        if not value:
            continue
        head = value[:_BOUNDARY_WINDOW]
        tail = value[-_BOUNDARY_WINDOW:]
        prepared.append(
            _PreparedBoundaryComponent(
                head=head,
                tail=tail,
            )
        )
    return tuple(prepared)


def _boundary_category_possible(category: str, spaced: str, compact: str) -> bool:
    """Return whether one edge-local join can complete a category signature."""

    encoded = any(_BOUNDARY_ENCODING_HINT.search(value) is not None for value in (spaced, compact))
    if category == "contact":
        return any(_contact_boundary_hint(value) for value in (spaced, compact))
    if category == "money":
        has_currency = encoded or any(_currency_boundary_hint(value) for value in (spaced, compact))
        return has_currency and any(
            _BOUNDARY_NUMBER_HINT.search(value) is not None for value in (spaced, compact)
        )
    pattern = {
        "instruction": _BOUNDARY_INSTRUCTION_HINT,
        "policy": _BOUNDARY_POLICY_HINT,
        "giving": _BOUNDARY_GIVING_HINT,
        "solicitation": _BOUNDARY_SOLICITATION_HINT,
    }[category]
    return encoded or any(pattern.search(value) is not None for value in (spaced, compact))


def _ordered_boundary_candidates(
    first: _PreparedBoundaryComponent,
    second: _PreparedBoundaryComponent,
    categories: frozenset[str],
) -> Iterator[tuple[str, frozenset[str], str, str]]:
    for category in categories:
        window = _BOUNDARY_CATEGORY_WINDOWS[category]
        first_edge = first.tail[-window:]
        second_edge = second.head[:window]
        compact_first = first_edge.rstrip(" \t\r\n.,;:!?")
        compact_second = second_edge.lstrip(" \t\r\n.,;:!?")
        spaced = f"{first_edge} {second_edge}"
        compact = f"{compact_first}{compact_second}"
        if category == "contact" and all(
            edge.strip().isascii() and edge.strip().isdecimal()
            for edge in (first_edge, second_edge)
        ):
            continue
        if not _boundary_category_possible(category, spaced, compact):
            continue
        possible = frozenset({category})
        yield spaced, possible, first.tail, second.head
        if compact != spaced and not (
            category == "contact" and first_edge.rstrip().endswith((".", "!", "?", ";"))
        ):
            yield compact, possible, first.tail, second.head


def _iter_pairwise_boundary_candidates(
    values: Sequence[str],
    categories: frozenset[str],
) -> Iterator[tuple[str, frozenset[str], str, str]]:
    prepared = _prepare_boundary_components(values)
    for left_index, left in enumerate(prepared):
        for right in prepared[left_index + 1 :]:
            yield from _ordered_boundary_candidates(left, right, categories)
            yield from _ordered_boundary_candidates(right, left, categories)


def _iter_cross_boundary_candidates(
    base_values: Sequence[str],
    extra_values: Sequence[str],
    categories: frozenset[str],
) -> Iterator[tuple[str, frozenset[str], str, str]]:
    """Yield hinted pairs where at least one component is category-specific."""

    base = _prepare_boundary_components(base_values)
    extras = _prepare_boundary_components(extra_values)
    for base_value in base:
        for extra_value in extras:
            yield from _ordered_boundary_candidates(base_value, extra_value, categories)
            yield from _ordered_boundary_candidates(extra_value, base_value, categories)
    for left_index, left in enumerate(extras):
        for right in extras[left_index + 1 :]:
            yield from _ordered_boundary_candidates(left, right, categories)
            yield from _ordered_boundary_candidates(right, left, categories)


def _boundary_batch_unsafe(category: str, values: Sequence[str]) -> bool:
    document = _BOUNDARY_BATCH_SENTINEL.join(values)
    if category == "contact":
        return contains_contact_like_text(document)
    if category == "instruction":
        return contains_instruction_like_text(document)
    if category == "policy":
        return contains_policy_control_like_text(document)
    if category == "solicitation":
        return contains_solicitation_language(document)
    if category == "money":
        return bool(money_expressions(document))
    raise ValueError(f"unsupported boundary category: {category}")


def _boundary_views_unsafe(
    views: Iterable[tuple[str, frozenset[str], str, str]],
    donor: DonorRecord,
    categories: frozenset[str],
) -> bool:
    """Scan lexically relevant pair boundaries in isolated, bounded batches."""

    batched_categories = categories - {"giving"}
    seen: dict[str, set[str]] = {category: set() for category in batched_categories}
    batches: dict[str, list[str]] = {category: [] for category in batched_categories}
    giving_seen: set[tuple[str, str, str]] = set()

    def add_to_batch(category: str, value: str) -> bool:
        if value in seen[category]:
            return False
        seen[category].add(value)
        batch = batches[category]
        batch.append(value)
        if len(batch) < _BOUNDARY_BATCH_SIZE:
            return False
        unsafe = _boundary_batch_unsafe(category, batch)
        batch.clear()
        return unsafe

    for value, possible, first, second in views:
        if "contact" in possible and add_to_batch("contact", value):
            return True
        if "instruction" in possible and add_to_batch("instruction", value):
            return True
        if "policy" in possible and add_to_batch("policy", value):
            return True
        if "giving" in possible:
            giving_key = (value, first, second)
            if giving_key not in giving_seen:
                giving_seen.add(giving_key)
                if (
                    contains_giving_history_like_text(value, donor)
                    and not contains_giving_history_like_text(first, donor)
                    and not contains_giving_history_like_text(second, donor)
                ):
                    return True
        if "solicitation" in possible and add_to_batch("solicitation", value):
            return True
        if "money" in possible and add_to_batch("money", value):
            return True
    return any(
        batch and _boundary_batch_unsafe(category, batch) for category, batch in batches.items()
    )


def _component_security_views(values: Sequence[str]) -> tuple[tuple[str, ...], ...]:
    return tuple(security_views(value) for value in values if value)


def _compact_full_literal_forms(view: str) -> tuple[str, ...]:
    compact = "".join(character for character in view if not character.isspace()).casefold()
    stripped = compact.strip(".,;:!?")
    return tuple(dict.fromkeys(value for value in (compact, stripped) if value))


def _compact_literal_forms(view: str) -> tuple[str, ...]:
    token_forms = (
        form
        for token in view.split()
        for form in (
            "".join(character for character in token if not character.isspace()).casefold(),
            "".join(character for character in token if not character.isspace())
            .casefold()
            .strip(".,;:!?"),
        )
    )
    return tuple(dict.fromkeys((*_compact_full_literal_forms(view), *token_forms)))


def _component_literal_forms(
    component_views: Sequence[Sequence[str]],
) -> tuple[tuple[str, ...], ...]:
    return tuple(
        tuple(dict.fromkeys(form for view in views for form in _compact_literal_forms(view)))
        for views in component_views
    )


_BOUNDARY_RELEVANT_DECODED_CHARACTERS = frozenset("@.:/\\+[](){}<>#%")


def _decoded_escape_is_boundary_relevant(decoded: str) -> bool:
    """Keep only decoded characters that can create a protected-data boundary."""

    return any(
        is_unsafe_text_character(character)
        or character in _BOUNDARY_RELEVANT_DECODED_CHARACTERS
        or character.isdecimal()
        or unicodedata.category(character) == "Sc"
        for character in decoded
    )


def _split_escape_is_effective(value: str) -> bool:
    """Return whether a valid escape decodes to boundary-relevant content."""

    folded = value.casefold()
    if folded.startswith("%u"):
        code_point = int(folded[2:], 16)
        if code_point > 0x10FFFF or 0xD800 <= code_point <= 0xDFFF:
            return True
        decoded = chr(code_point)
    elif folded.startswith("%"):
        try:
            decoded = bytes.fromhex(folded.replace("%", "")).decode("utf-8")
        except (UnicodeDecodeError, ValueError):
            return True
    elif folded.startswith("&"):
        decoded = unescape(value)
        if decoded == value:
            return False
    elif folded.startswith("\\x") or folded.startswith("\\u"):
        code_point = int(folded[2:], 16)
        if code_point > 0x10FFFF or 0xD800 <= code_point <= 0xDFFF:
            return True
        decoded = chr(code_point)
    elif folded.startswith("\\"):
        code_point = int(folded[1:], 8)
        if code_point > 0x10FFFF or 0xD800 <= code_point <= 0xDFFF:
            return True
        decoded = chr(code_point)
    else:
        return False
    return bool(decoded) and _decoded_escape_is_boundary_relevant(decoded)


@cache
def _effective_html_entity_prefixes() -> frozenset[str]:
    """Return lowercase prefixes of named entities that can affect a safety boundary."""

    names: set[str] = set()
    for registered_name in html5:
        name = registered_name.rstrip(";").casefold()
        if not (2 <= len(name) <= 32 and name.isascii() and name.isalnum()):
            continue
        encoded = f"&{name};"
        decoded = unescape(encoded)
        if decoded != encoded and _decoded_escape_is_boundary_relevant(decoded):
            names.add(name)
    return frozenset(name[:length] for name in names for length in range(1, len(name) + 1))


def _escape_prefix_can_complete(value: str) -> bool:
    """Recognize a strict prefix of one supported bounded escape token."""

    if not value or len(value) >= _MAX_SPLIT_ESCAPE_LENGTH:
        return False
    folded = value.casefold()
    if folded.startswith("%"):
        if folded == "%":
            return True
        if folded.startswith("%u"):
            digits = folded[2:]
            return len(digits) <= 4 and all(character in "0123456789abcdef" for character in digits)
        index = 0
        groups = 0
        while index < len(folded):
            if folded[index] != "%" or groups >= 4:
                return False
            index += 1
            digit_start = index
            while index < len(folded) and index - digit_start < 2:
                if folded[index] not in "0123456789abcdef":
                    return False
                index += 1
            if index - digit_start < 2:
                return index == len(folded)
            groups += 1
            if index < len(folded) and folded[index] != "%":
                return False
        return False
    if folded.startswith("&"):
        if folded == "&":
            return True
        if folded.startswith("&#x"):
            digits = folded[3:]
            return len(digits) <= 6 and all(character in "0123456789abcdef" for character in digits)
        if folded.startswith("&#"):
            digits = folded[2:]
            return len(digits) <= 7 and (not digits or (digits.isdecimal() and digits.isascii()))
        name = folded[1:]
        return name in _effective_html_entity_prefixes()
    if value.startswith("\\"):
        if value == "\\":
            return True
        marker = value[1]
        if marker in {"x", "X"}:
            digits = value[2:]
            limit = 2
            alphabet = "0123456789abcdefABCDEF"
        elif marker == "u":
            digits = value[2:]
            limit = 4
            alphabet = "0123456789abcdefABCDEF"
        elif marker == "U":
            digits = value[2:]
            limit = 8
            alphabet = "0123456789abcdefABCDEF"
        else:
            digits = value[1:]
            limit = 3
            alphabet = "01234567"
        return len(digits) <= limit and all(character in alphabet for character in digits)
    return False


def _components_complete_split_escape(
    component_forms: Sequence[Sequence[str]],
) -> bool:
    """Fail closed when distinct fields jointly complete one supported escape token."""

    frontiers: dict[str, list[int]] = {}
    queue: list[tuple[str, int]] = []
    for component_index, forms in enumerate(component_forms):
        component_mask = 1 << component_index
        for form in forms:
            for marker in ("%", "&", "\\"):
                search_start = max(0, len(form) - _MAX_SPLIT_ESCAPE_LENGTH)
                marker_index = form.find(marker, search_start)
                while marker_index >= 0:
                    current_index = marker_index
                    suffix = form[current_index:]
                    marker_index = form.find(marker, current_index + 1)
                    if marker == "%" and form[current_index - 1 : current_index].isdecimal():
                        continue
                    if _SPLIT_SECURITY_ESCAPE.match(suffix) is not None:
                        continue
                    if not _escape_prefix_can_complete(suffix):
                        continue
                    masks = frontiers.setdefault(suffix, [])
                    if component_mask not in masks:
                        masks.append(component_mask)
                        queue.append((suffix, component_mask))

    queue_index = 0
    visited_states = len(queue)
    while queue_index < len(queue):
        prefix, used_mask = queue[queue_index]
        queue_index += 1
        remaining = _MAX_SPLIT_ESCAPE_LENGTH - len(prefix)
        for component_index, forms in enumerate(component_forms):
            component_mask = 1 << component_index
            if used_mask & component_mask:
                continue
            for form in forms:
                candidate = prefix + form[:remaining]
                match = _SPLIT_SECURITY_ESCAPE.match(candidate)
                if match is not None and _split_escape_is_effective(match.group(0)):
                    return True
                if not _escape_prefix_can_complete(candidate):
                    continue
                next_mask = used_mask | component_mask
                masks = frontiers.setdefault(candidate, [])
                if any(existing_mask & next_mask == existing_mask for existing_mask in masks):
                    continue
                frontiers[candidate] = [
                    existing_mask
                    for existing_mask in masks
                    if next_mask & existing_mask != next_mask
                ]
                frontiers[candidate].append(next_mask)
                queue.append((candidate, next_mask))
                visited_states += 1
                if visited_states > _MAX_LITERAL_CLOSURE_STATES:
                    return True
    return False


def _literal_target_forms(literal: str) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            form
            for view in security_views(literal)
            for form in _compact_full_literal_forms(view)
            if len(form) >= 2
        )
    )


def _target_form_reconstructed(
    target: str,
    component_forms: Sequence[Sequence[str]],
) -> bool:
    """Find a token-boundary segmentation of one exact sensitive literal."""

    form_sources: dict[str, int] = {}
    for component_index, forms in enumerate(component_forms):
        for form in forms:
            if len(form) <= len(target):
                form_sources[form] = form_sources.get(form, 0) | (1 << component_index)
    form_lengths = tuple(sorted({len(form) for form in form_sources}))
    if not form_lengths:
        return False

    transitions: list[tuple[tuple[int, int], ...]] = []
    reachable = [False] * (len(target) + 1)
    reachable[0] = True
    for position in range(len(target)):
        found_transitions: list[tuple[int, int]] = []
        for form_length in form_lengths:
            end = position + form_length
            if end > len(target):
                break
            sources = form_sources.get(target[position:end])
            if sources is not None:
                found_transitions.append((end, sources))
                if reachable[position]:
                    reachable[end] = True
        transitions.append(tuple(found_transitions))
    if not reachable[-1]:
        return False

    frontiers: list[list[int]] = [[] for _ in range(len(target) + 1)]
    frontiers[0] = [0]
    visited_states = 1
    for position, position_transitions in enumerate(transitions):
        if not frontiers[position]:
            continue
        for next_position, source_mask in position_transitions:
            for used_mask in frontiers[position]:
                available = source_mask & ~used_mask
                while available:
                    component_mask = available & -available
                    available -= component_mask
                    next_mask = used_mask | component_mask
                    if next_position == len(target) and next_mask.bit_count() >= 2:
                        return True
                    existing_masks = frontiers[next_position]
                    if any(
                        existing_mask & next_mask == existing_mask
                        for existing_mask in existing_masks
                    ):
                        continue
                    frontiers[next_position] = [
                        existing_mask
                        for existing_mask in existing_masks
                        if next_mask & existing_mask != next_mask
                    ]
                    frontiers[next_position].append(next_mask)
                    visited_states += 1
                    if visited_states > _MAX_LITERAL_CLOSURE_STATES:
                        return True
    return False


def _target_form_reconstruction_masks(
    target: str,
    component_forms: Sequence[Sequence[str]],
    *,
    minimum_components: int,
) -> tuple[int, ...] | None:
    """Return distinct-component masks that exactly reconstruct one short token."""

    transitions: list[tuple[tuple[int, int], ...]] = []
    for position in range(len(target)):
        position_transitions: set[tuple[int, int]] = set()
        for component_index, forms in enumerate(component_forms):
            component_mask = 1 << component_index
            position_transitions.update(
                (position + len(form), component_mask)
                for form in forms
                if form and target.startswith(form, position)
            )
        transitions.append(tuple(position_transitions))

    frontiers: list[set[int]] = [set() for _ in range(len(target) + 1)]
    frontiers[0].add(0)
    visited_states = 1
    for position, stored_transitions in enumerate(transitions):
        for used_mask in tuple(frontiers[position]):
            for next_position, component_mask in stored_transitions:
                if used_mask & component_mask:
                    continue
                next_mask = used_mask | component_mask
                if next_mask in frontiers[next_position]:
                    continue
                frontiers[next_position].add(next_mask)
                visited_states += 1
                if visited_states > _MAX_COMPONENT_GRAMMAR_STATES:
                    return None
    return tuple(sorted(mask for mask in frontiers[-1] if mask.bit_count() >= minimum_components))


def _components_reconstruct_control_language(
    component_forms: Sequence[Sequence[str]],
) -> bool:
    """Close high-risk control lexemes split across three or more structured fields."""

    target_cache: dict[str, tuple[bool, bool]] = {}
    encoded_forms_present = any(
        any(marker in form for marker in ("%", "&", "\\"))
        for forms in component_forms
        for form in forms
    )

    def target_status(target: str) -> tuple[bool, bool]:
        if target not in target_cache:
            present = any(target == form for forms in component_forms for form in forms)
            minimum_components = _COMPONENT_MINIMUM_PLAIN_RECONSTRUCTION_COMPONENTS.get(
                target,
                2,
            )
            if minimum_components == 2:
                plain_reconstructed = _target_form_reconstructed(target, component_forms)
            else:
                reconstruction_masks = _target_form_reconstruction_masks(
                    target,
                    component_forms,
                    minimum_components=minimum_components,
                )
                plain_reconstructed = reconstruction_masks is None or bool(reconstruction_masks)
            reconstructed = plain_reconstructed or (
                encoded_forms_present
                and _encoded_target_form_reconstructed(target, component_forms)
            )
            target_cache[target] = present or reconstructed, reconstructed
        return target_cache[target]

    def group_reconstructed(left: Sequence[str], right: Sequence[str]) -> bool:
        left_status = tuple(target_status(target) for target in left)
        right_status = tuple(target_status(target) for target in right)
        return (
            any(present for present, _ in left_status)
            and any(present for present, _ in right_status)
            and (
                any(reconstructed for _, reconstructed in left_status)
                or any(reconstructed for _, reconstructed in right_status)
            )
        )

    if any(
        target_status(target)[1]
        for target in (*_COMPONENT_HIGH_RISK_TARGETS, *_COMPONENT_SOLICITATION_WORDS)
    ) or any(group_reconstructed(left, right) for left, right in _COMPONENT_INSTRUCTION_GROUPS):
        return True

    has_number = any(
        form in _COMPONENT_NUMBER_WORDS or (form.isascii() and form.isdecimal())
        for forms in component_forms
        for form in forms
    )
    if has_number and any(target_status(target)[1] for target in _COMPONENT_GIVING_VERBS):
        return True
    if not has_number:
        return False
    currency_targets = (
        *(code.casefold() for code in sorted(ISO_4217_ACTIVE_CODE_SET)),
        *_COMPONENT_CURRENCY_NAMES,
    )
    return any(target_status(target)[1] for target in currency_targets)


def _components_reconstruct_literal(
    literal: str,
    component_forms: Sequence[Sequence[str]],
) -> bool:
    encoded_forms_present = any(
        any(marker in form for marker in ("%", "&", "\\"))
        for forms in component_forms
        for form in forms
    )
    return any(
        _target_form_reconstructed(target, component_forms)
        or (encoded_forms_present and _encoded_target_form_reconstructed(target, component_forms))
        for target in _literal_target_forms(literal)
    )


_EncodedTargetState = tuple[int, str, int, bool]


def _encoded_character_variants(character: str) -> tuple[str, ...]:
    code_point = ord(character)
    percent_encoded = "".join(f"%{byte:02x}" for byte in character.encode("utf-8"))
    variants = [
        character,
        percent_encoded,
        f"&#{code_point};",
        f"&#x{code_point:x};",
        f"\\U{code_point:08x}",
    ]
    entity_name = codepoint2name.get(code_point)
    if entity_name is not None:
        variants.append(f"&{entity_name};")
    if code_point <= 0xFF:
        variants.append(f"\\x{code_point:02x}")
    if code_point <= 0xFFFF:
        variants.extend((f"\\u{code_point:04x}", f"%u{code_point:04x}"))
    if code_point <= 0o777:
        variants.append(f"\\{code_point:03o}")
    return tuple(dict.fromkeys(variant.casefold() for variant in variants))


def _advance_encoded_target_states(
    states: frozenset[_EncodedTargetState],
    fragment: str,
    variants: Sequence[Sequence[str]],
) -> frozenset[_EncodedTargetState]:
    target_length = len(variants)
    current = states
    for character in fragment:
        following: set[_EncodedTargetState] = set()
        for position, variant, offset, used_encoding in current:
            if position >= target_length or variant[offset] != character:
                continue
            next_offset = offset + 1
            if next_offset < len(variant):
                following.add((position, variant, next_offset, used_encoding))
            elif position + 1 == target_length:
                following.add((target_length, "", 0, used_encoding))
            else:
                following.update(
                    (
                        position + 1,
                        next_variant,
                        0,
                        used_encoding or next_variant != variants[position + 1][0],
                    )
                    for next_variant in variants[position + 1]
                )
        if not following:
            return frozenset()
        current = frozenset(following)
    return current


def _encoded_target_form_reconstructed(
    target: str,
    component_forms: Sequence[Sequence[str]],
) -> bool:
    """Match token fragments after a conceptual join and one supported decode layer."""

    variants = tuple(_encoded_character_variants(character) for character in target)
    if not variants:
        return False
    initial_states = frozenset(
        (0, variant, 0, variant != variants[0][0]) for variant in variants[0]
    )
    transition_cache: dict[
        tuple[frozenset[_EncodedTargetState], str],
        frozenset[_EncodedTargetState],
    ] = {}
    frontiers: dict[frozenset[_EncodedTargetState], list[int]] = {}
    queue: list[tuple[frozenset[_EncodedTargetState], int]] = []

    def advance(
        states: frozenset[_EncodedTargetState],
        form: str,
    ) -> frozenset[_EncodedTargetState]:
        key = (states, form)
        if key not in transition_cache:
            transition_cache[key] = _advance_encoded_target_states(states, form, variants)
        return transition_cache[key]

    for component_index, forms in enumerate(component_forms):
        for form in forms:
            if not form:
                continue
            next_states = advance(initial_states, form)
            if not next_states:
                continue
            mask = 1 << component_index
            next_states = frozenset(state for state in next_states if state[0] < len(target))
            if not next_states:
                continue
            existing_masks = frontiers.setdefault(next_states, [])
            if mask not in existing_masks:
                existing_masks.append(mask)
                queue.append((next_states, mask))

    queue_index = 0
    visited_states = len(queue)
    while queue_index < len(queue):
        states, used_mask = queue[queue_index]
        queue_index += 1
        for component_index, forms in enumerate(component_forms):
            component_mask = 1 << component_index
            if used_mask & component_mask:
                continue
            for form in forms:
                if not form:
                    continue
                next_states = advance(states, form)
                if not next_states:
                    continue
                next_mask = used_mask | component_mask
                if any(
                    position == len(target) and used_encoding
                    for position, _variant, _offset, used_encoding in next_states
                ):
                    return True
                next_states = frozenset(state for state in next_states if state[0] < len(target))
                if not next_states:
                    continue
                existing_masks = frontiers.setdefault(next_states, [])
                if any(
                    existing_mask & next_mask == existing_mask for existing_mask in existing_masks
                ):
                    continue
                frontiers[next_states] = [
                    existing_mask
                    for existing_mask in existing_masks
                    if next_mask & existing_mask != next_mask
                ]
                frontiers[next_states].append(next_mask)
                queue.append((next_states, next_mask))
                visited_states += 1
                if visited_states > _MAX_LITERAL_CLOSURE_STATES:
                    return True
    return False


def _ascii_tld(token: str) -> str | None:
    try:
        return token.encode("idna").decode("ascii").casefold()
    except UnicodeError:
        return None


@cache
def _iana_tld_reconstruction_index() -> tuple[dict[str, str], frozenset[str]]:
    target_to_ascii: dict[str, str] = {}
    for ascii_tld in sorted(IANA_ROOT_ZONE_TLD_SET):
        target_to_ascii[ascii_tld] = ascii_tld
        if ascii_tld.startswith("xn--"):
            try:
                unicode_tld = ascii_tld.encode("ascii").decode("idna").casefold()
            except UnicodeError:
                continue
            target_to_ascii[unicode_tld] = ascii_tld
    prefixes = frozenset(
        target[:length] for target in target_to_ascii for length in range(1, len(target) + 1)
    )
    return target_to_ascii, prefixes


def _root_tld_reconstruction_transitions(
    component_forms: Sequence[Sequence[str]],
) -> tuple[_GrammarTransition, ...] | None:
    """Reconstruct all official TLDs in one bounded, mask-preserving traversal."""

    target_to_ascii, prefixes = _iana_tld_reconstruction_index()
    maximum_length = max(map(len, target_to_ascii))
    form_sources: dict[str, int] = {}
    for component_index, forms in enumerate(component_forms):
        for form in forms:
            if form and len(form) <= maximum_length:
                form_sources[form] = form_sources.get(form, 0) | (1 << component_index)

    frontiers: dict[str, list[int]] = {}
    queue: list[tuple[str, int]] = []
    for form, source_bits in form_sources.items():
        if form not in prefixes:
            continue
        available = source_bits
        while available:
            component_mask = available & -available
            available -= component_mask
            frontiers.setdefault(form, []).append(component_mask)
            queue.append((form, component_mask))

    terminal_masks: dict[str, list[int]] = {}
    queue_index = 0
    visited_states = len(queue)
    while queue_index < len(queue):
        prefix, used_mask = queue[queue_index]
        queue_index += 1
        ascii_tld = target_to_ascii.get(prefix)
        if ascii_tld is not None and used_mask.bit_count() >= 2:
            existing_terminal_masks = terminal_masks.setdefault(ascii_tld, [])
            if not any(
                existing_mask & used_mask == existing_mask
                for existing_mask in existing_terminal_masks
            ):
                terminal_masks[ascii_tld] = [
                    existing_mask
                    for existing_mask in existing_terminal_masks
                    if used_mask & existing_mask != used_mask
                ]
                terminal_masks[ascii_tld].append(used_mask)

        for form, source_bits in form_sources.items():
            candidate = prefix + form
            if candidate not in prefixes:
                continue
            available = source_bits & ~used_mask
            while available:
                component_mask = available & -available
                available -= component_mask
                next_mask = used_mask | component_mask
                existing_masks = frontiers.setdefault(candidate, [])
                if any(
                    existing_mask & next_mask == existing_mask for existing_mask in existing_masks
                ):
                    continue
                frontiers[candidate] = [
                    existing_mask
                    for existing_mask in existing_masks
                    if next_mask & existing_mask != next_mask
                ]
                frontiers[candidate].append(next_mask)
                queue.append((candidate, next_mask))
                visited_states += 1
                if visited_states > _MAX_COMPONENT_GRAMMAR_STATES:
                    return None

    return tuple(
        ((ascii_tld,), mask)
        for ascii_tld in sorted(terminal_masks)
        for mask in sorted(terminal_masks[ascii_tld])
    )


def _contact_fragment_clause_prefix(text: str, end: int) -> str:
    clause_start = 0
    for separator in (".", "!", "?", ";", "\n", _SECURITY_SENTENCE_BREAK):
        separator_index = text.rfind(separator, 0, end)
        if separator_index >= 0:
            clause_start = max(clause_start, separator_index + len(separator))
    return text[clause_start:end]


def _contact_fragment_dot_word_is_cued(text: str, end: int) -> bool:
    clause_prefix = _contact_fragment_clause_prefix(text, end)
    if _CONTACT_WEB_CUE.search(clause_prefix) is not None:
        return True
    return any(
        re.search(r"(?:@|\bat\b)", clause_prefix[match.end() :], re.IGNORECASE) is not None
        for match in _CONTACT_EMAIL_CUE.finditer(clause_prefix)
    )


def _canonical_contact_fragment_tokens(
    view: str,
    *,
    allow_cross_component_cued_dot_words: bool,
) -> tuple[str, ...]:
    canonical = _CONTACT_FRAGMENT_STRONG_AT.sub(" @ ", view)
    canonical = _CONTACT_FRAGMENT_STRONG_DOT.sub(" . ", canonical)
    canonical = _CONTACT_FRAGMENT_STRONG_COLON.sub(" : ", canonical)
    canonical = canonical.translate(
        {
            ord("\u3002"): ".",
            ord("\uff0e"): ".",
            ord("\uff61"): ".",
        }
    )
    tokens: list[str] = []
    for match in _CONTACT_FRAGMENT_TOKEN.finditer(canonical):
        token = match.group(0)
        folded = token.casefold()
        if folded == "at":
            tokens.append("@")
        elif folded == "dot" or (
            folded in {"period", "point"}
            and (
                allow_cross_component_cued_dot_words
                or _contact_fragment_dot_word_is_cued(canonical, match.start())
            )
        ):
            tokens.append(".")
        elif folded == "colon":
            tokens.append(":")
        elif token and all(character.isdecimal() for character in token):
            tokens.append("".join(str(unicodedata.decimal(character)) for character in token))
        else:
            tokens.append(folded)
    return tuple(tokens)


def _contact_fragment_sequences(
    component_views: Sequence[Sequence[str]],
    *,
    maximum_tokens: int,
    extract_numeric_tokens: bool = True,
) -> tuple[tuple[tuple[str, ...], ...], ...]:
    allow_cross_component_cued_dot_words = any(
        _CONTACT_STANDALONE_CUE.fullmatch(view) is not None
        for views in component_views
        for view in views
    )
    component_sequences: list[tuple[tuple[str, ...], ...]] = []
    for views in component_views:
        sequences: list[tuple[str, ...]] = []
        for view in views:
            tokens = _canonical_contact_fragment_tokens(
                view,
                allow_cross_component_cued_dot_words=allow_cross_component_cued_dot_words,
            )
            if len(view) <= 96 and 1 <= len(tokens) <= maximum_tokens:
                sequences.append(tokens)
            if extract_numeric_tokens:
                sequences.extend(
                    (token,)
                    for token in tokens
                    if token.isascii() and token.isdecimal() and len(token) <= 3
                )
        component_sequences.append(tuple(dict.fromkeys(sequences)))
    return tuple(component_sequences)


_GrammarState = TypeVar("_GrammarState", bound=Hashable)
_GrammarTransition = tuple[tuple[str, ...], int]


def _contact_composite_transitions(
    component_forms: Sequence[Sequence[str]],
    component_sequences: Sequence[Sequence[tuple[str, ...]]],
) -> tuple[tuple[_GrammarTransition, ...], tuple[_GrammarTransition, ...], bool]:
    """Build mask-preserving contact markers and targeted split labels."""

    markers: list[_GrammarTransition] = []
    for target, symbol in (("at", "@"), ("dot", "."), ("colon", ":")):
        masks = _target_form_reconstruction_masks(
            target,
            component_forms,
            minimum_components=2,
        )
        if masks is None:
            return (), (), True
        markers.extend(((symbol,), mask) for mask in masks)

    base_markers = {
        sequence
        for sequences in component_sequences
        for sequence in sequences
        if sequence in {("@",), (".",), (":",)}
    }
    composite_markers = {sequence for sequence, _ in markers}
    if not base_markers | composite_markers:
        return tuple(markers), (), False

    fragments = tuple(
        dict.fromkeys(
            (
                *(
                    (sequence[0], 1 << component_index)
                    for component_index, sequences in enumerate(component_sequences)
                    for sequence in sequences
                    if len(sequence) == 1
                    and sequence[0] not in {"@", ".", ":"}
                    and 1 <= len(sequence[0]) <= 63
                    and all(
                        character == "-" or unicodedata.category(character)[:1] in {"L", "M", "N"}
                        for character in sequence[0]
                    )
                ),
                *(
                    (form, 1 << component_index)
                    for component_index, forms in enumerate(component_forms)
                    for form in forms
                    if "-" in form
                    and 1 <= len(form) <= 63
                    and all(
                        character == "-" or unicodedata.category(character)[:1] in {"L", "M", "N"}
                        for character in form
                    )
                ),
            )
        )
    )

    # A non-terminal domain label carries no semantic state beyond validity. Two
    # fragments are sufficient for that transition: if a longer segmentation is
    # possible, any valid prefix pair already supplies a syntactic label and the
    # remaining components may stay unused. Terminal labels are different because
    # they must be a real root-zone TLD, so those are reconstructed exactly below.
    generic_label_masks: set[int] = set()
    labels: set[_GrammarTransition] = set()
    has_terminal_label = any(
        (ascii_tld := _ascii_tld(sequence[0])) is not None
        and (ascii_tld in IANA_ROOT_ZONE_TLD_SET or ascii_tld.startswith("xn--"))
        for sequences in component_sequences
        for sequence in sequences
        if len(sequence) == 1
    )
    for left, left_mask in fragments:
        for right, right_mask in fragments:
            if left_mask & right_mask or len(left) + len(right) > 63:
                continue
            candidate = left + right
            if not _valid_domain_label(candidate):
                continue
            source_mask = left_mask | right_mask
            ascii_label = _ascii_tld(candidate)
            is_terminal = ascii_label is not None and (
                ascii_label in IANA_ROOT_ZONE_TLD_SET or ascii_label.startswith("xn--")
            )
            if is_terminal:
                labels.add(((candidate,), source_mask))
                has_terminal_label = True
            else:
                generic_label_masks.add(source_mask)

    tld_transitions = _root_tld_reconstruction_transitions(component_forms)
    if tld_transitions is None:
        return (), (), True
    if tld_transitions:
        has_terminal_label = True
        labels.update(tld_transitions)

    if has_terminal_label:
        labels.update((("qzlabel",), mask) for mask in generic_label_masks)
    return tuple(sorted(set(markers))), tuple(sorted(labels)), False


def _component_grammar_reconstructed(
    component_sequences: Sequence[Sequence[tuple[str, ...]]],
    initial_state: _GrammarState,
    advance: Callable[[_GrammarState, tuple[str, ...]], _GrammarState | None],
    accepting: Callable[[_GrammarState], bool],
    *,
    extra_transitions: Sequence[_GrammarTransition] = (),
) -> bool:
    transitions = tuple(
        dict.fromkeys(
            (
                *(transition for transition in extra_transitions),
                *(
                    (sequence, 1 << component_index)
                    for component_index, sequences in enumerate(component_sequences)
                    for sequence in sequences
                ),
            )
        )
    )
    frontiers: dict[_GrammarState, list[int]] = {initial_state: [0]}
    queue: list[tuple[_GrammarState, int]] = [(initial_state, 0)]
    queue_index = 0
    visited_states = 1
    while queue_index < len(queue):
        state, used_mask = queue[queue_index]
        queue_index += 1
        for sequence, source_mask in transitions:
            if used_mask & source_mask:
                continue
            next_state = advance(state, sequence)
            if next_state is None:
                continue
            next_mask = used_mask | source_mask
            if accepting(next_state) and next_mask.bit_count() >= 2:
                return True
            existing_masks = frontiers.setdefault(next_state, [])
            if any(existing_mask & next_mask == existing_mask for existing_mask in existing_masks):
                continue
            frontiers[next_state] = [
                existing_mask
                for existing_mask in existing_masks
                if next_mask & existing_mask != next_mask
            ]
            frontiers[next_state].append(next_mask)
            queue.append((next_state, next_mask))
            visited_states += 1
            if visited_states > _MAX_COMPONENT_GRAMMAR_STATES:
                return True
    return False


_DomainGrammarState = tuple[int, int, bool]


def _valid_domain_label(token: str) -> bool:
    if token in {"@", ".", ":"} or len(token) > 63:
        return False
    try:
        ascii_label = token.encode("idna").decode("ascii").casefold()
    except UnicodeError:
        return False
    return re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", ascii_label) is not None


def _advance_domain_grammar(
    state: _DomainGrammarState,
    sequence: tuple[str, ...],
) -> _DomainGrammarState | None:
    phase, label_count, last_label_is_tld = state
    for token in sequence:
        if phase == 0:
            if not _valid_domain_label(token):
                return None
            phase = 1
        elif phase == 1:
            if token != "@":
                return None
            phase = 2
        elif phase == 2:
            if not _valid_domain_label(token) or label_count >= 10:
                return None
            label_count += 1
            ascii_tld = _ascii_tld(token)
            last_label_is_tld = ascii_tld is not None and (
                ascii_tld in IANA_ROOT_ZONE_TLD_SET or ascii_tld.startswith("xn--")
            )
            phase = 3
        elif token == ".":
            phase = 2
            last_label_is_tld = False
        else:
            return None
    return phase, label_count, last_label_is_tld


def _domain_grammar_accepting(state: _DomainGrammarState) -> bool:
    phase, label_count, last_label_is_tld = state
    return phase == 3 and label_count >= 2 and last_label_is_tld


_IPv4GrammarState = tuple[int, bool]


def _advance_ipv4_grammar(
    state: _IPv4GrammarState,
    sequence: tuple[str, ...],
) -> _IPv4GrammarState | None:
    octet_count, expect_octet = state
    for token in sequence:
        if expect_octet:
            if (
                not token.isascii()
                or not token.isdecimal()
                or (len(token) > 1 and token.startswith("0"))
                or not 0 <= int(token) <= 255
            ):
                return None
            octet_count += 1
            if octet_count > 4:
                return None
            expect_octet = False
        elif token == ".":
            expect_octet = True
        else:
            return None
    return octet_count, expect_octet


def _ipv4_grammar_accepting(state: _IPv4GrammarState) -> bool:
    octet_count, expect_octet = state
    return octet_count == 4 and not expect_octet


def _advance_ipv6_grammar(value: str, sequence: tuple[str, ...]) -> str | None:
    if any(token != ":" and re.fullmatch(r"[0-9a-f]{1,4}", token) is None for token in sequence):
        return None
    candidate = value + "".join(sequence)
    if len(candidate) > 45 or ":::" in candidate or candidate.count("::") > 1:
        return None
    return candidate


def _ipv6_grammar_accepting(value: str) -> bool:
    if ":" not in value:
        return False
    try:
        return isinstance(ipaddress.ip_address(value), ipaddress.IPv6Address)
    except ValueError:
        return False


_PHONE_WORD_VALUES = {
    "zero": "0",
    "oh": "0",
    "one": "1",
    "two": "2",
    "three": "3",
    "four": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8",
    "nine": "9",
}
_PHONE_STANDALONE_CUE = re.compile(
    r"\s*(?:\+|call|dial|extension|ext|mobile|phone|sms|tel|telephone|text)"
    r"(?:\s+(?:at|is|me|number|us))?\s*[.:#?!-]?\s*",
    re.IGNORECASE,
)
_PHONE_FRAGMENT_LABELS = (
    ("country", "code"),
    ("area", "code"),
    ("country",),
    ("exchange",),
    ("area",),
    ("line",),
)
_PHONE_INLINE_CUES = (
    ("our", "number", "is"),
    ("telephone", "number"),
    ("phone", "number"),
    ("extension",),
    ("telephone",),
    ("mobile",),
    ("phone",),
    ("call",),
    ("dial",),
    ("sms",),
    ("tel",),
    ("text",),
)
_PhoneFragmentOption = tuple[str, bool, bool, bool]


def _phone_fragment_option(sequence: tuple[str, ...]) -> _PhoneFragmentOption | None:
    """Parse one bounded numeric, word-digit, labeled, or directly cued component."""

    payload = sequence
    has_label = False
    has_cue = False
    for prefix in _PHONE_FRAGMENT_LABELS:
        if payload[: len(prefix)] == prefix:
            payload = payload[len(prefix) :]
            has_label = True
            break
    if not has_label:
        for cue_prefix in _PHONE_INLINE_CUES:
            if payload[: len(cue_prefix)] == cue_prefix:
                payload = payload[len(cue_prefix) :]
                has_cue = True
                break
    if payload[:1] == (":",):
        payload = payload[1:]
    if not payload:
        return None

    digits: list[str] = []
    has_word = False
    for token in payload:
        if token in _PHONE_WORD_VALUES:
            digits.append(_PHONE_WORD_VALUES[token])
            has_word = True
        elif token.isascii() and token.isdecimal():
            digits.append(token)
        else:
            return None
    value = "".join(digits)
    if not 1 <= len(value) <= 15:
        return None
    return value, has_word, has_cue, has_label


def _components_reconstruct_phone(
    component_sequences: Sequence[Sequence[tuple[str, ...]]],
    component_views: Sequence[Sequence[str]],
) -> bool:
    has_standalone_cue = any(
        _PHONE_STANDALONE_CUE.fullmatch(view) is not None
        for views in component_views
        for view in views
    )
    component_options = tuple(
        tuple(
            dict.fromkeys(
                option
                for sequence in sequences
                if (option := _phone_fragment_option(sequence)) is not None
            )
        )
        for sequences in component_sequences
    )

    reachable: set[tuple[int, int, bool, bool, bool]] = {(0, 0, False, has_standalone_cue, False)}
    for options in component_options:
        updated = set(reachable)
        for digit_count, component_count, has_word, has_cue, has_label in reachable:
            updated.update(
                (
                    digit_count + len(value),
                    component_count + 1,
                    has_word or contains_word,
                    has_cue or contains_cue,
                    has_label or contains_label,
                )
                for value, contains_word, contains_cue, contains_label in options
                if digit_count + len(value) <= 15
            )
        reachable = updated
        if any(
            7 <= digits <= 15 and count >= 2 and (has_word or has_cue or has_label)
            for digits, count, has_word, has_cue, has_label in reachable
        ):
            return True

    numeric_shapes: set[tuple[str, ...]] = {()}
    for options in component_options:
        component_groups = {value for value, *_ in options}
        numeric_shapes.update(
            tuple(sorted((*shape, group)))
            for shape in tuple(numeric_shapes)
            for group in component_groups
            if len(shape) < 3
        )
    if any(
        sorted(map(len, shape)) == [3, 3, 4]
        and all(group[0] in "23456789" for group in shape if len(group) == 3)
        for shape in numeric_shapes
    ):
        return True

    international_shapes: set[tuple[str | None, int, tuple[int, ...]]] = {(None, 0, ())}
    for options in component_options:
        updated_shapes = set(international_shapes)
        groups = {value for value, *_ in options}
        for country_code, national_digits, national_shape in international_shapes:
            for group in groups:
                total_digits = national_digits + len(group) + len(country_code or "")
                if (
                    group not in {"0", "00", "011"}
                    and len(national_shape) < 7
                    and total_digits <= 15
                ):
                    updated_shapes.add(
                        (
                            country_code,
                            national_digits + len(group),
                            tuple(sorted((*national_shape, len(group)))),
                        )
                    )
                if (
                    country_code is None
                    and group in E164_ASSIGNED_CALLING_CODE_SET
                    and national_digits + len(group) <= 15
                ):
                    updated_shapes.add((group, national_digits, national_shape))
        international_shapes = updated_shapes
    return any(
        country_code is not None
        and len(national_shape) >= 3
        and national_digits in E164_POSSIBLE_NATIONAL_LENGTHS[country_code]
        and national_digits + len(country_code) >= 7
        and national_digits + len(country_code) <= 15
        for country_code, national_digits, national_shape in international_shapes
    )


def _components_reconstruct_defanged_contact(
    component_views: Sequence[Sequence[str]],
    component_forms: Sequence[Sequence[str]],
) -> bool:
    """Detect fragment-shaped email, domain, IP, or phone grammars across fields."""

    short_sequences = _contact_fragment_sequences(component_views, maximum_tokens=5)
    standalone_cue_transitions = tuple(
        ((), 1 << component_index)
        for component_index, views in enumerate(component_views)
        if any(_CONTACT_STANDALONE_CUE.fullmatch(view) is not None for view in views)
    )
    marker_transitions, label_transitions, overflow = _contact_composite_transitions(
        component_forms,
        short_sequences,
    )
    if overflow:
        return True
    domain_transitions = (*marker_transitions, *label_transitions)
    if _component_grammar_reconstructed(
        short_sequences,
        (0, True),
        _advance_ipv4_grammar,
        _ipv4_grammar_accepting,
        extra_transitions=(
            *standalone_cue_transitions,
            *(transition for transition in marker_transitions if transition[0] == (".",)),
        ),
    ):
        return True
    if _component_grammar_reconstructed(
        short_sequences,
        (0, 0, False),
        _advance_domain_grammar,
        _domain_grammar_accepting,
        extra_transitions=(*standalone_cue_transitions, *domain_transitions),
    ) or _component_grammar_reconstructed(
        short_sequences,
        (2, 0, False),
        _advance_domain_grammar,
        _domain_grammar_accepting,
        extra_transitions=(*standalone_cue_transitions, *domain_transitions),
    ):
        return True
    ipv6_sequences = tuple(
        tuple(
            sequence
            for sequence in sequences
            if all(
                token == ":" or re.fullmatch(r"[0-9a-f]{1,4}", token) is not None
                for token in sequence
            )
        )
        for sequences in short_sequences
    )
    if any(":" in sequence for sequences in ipv6_sequences for sequence in sequences) and (
        _component_grammar_reconstructed(
            ipv6_sequences,
            "",
            _advance_ipv6_grammar,
            _ipv6_grammar_accepting,
            extra_transitions=(
                *standalone_cue_transitions,
                *(transition for transition in marker_transitions if transition[0] == (":",)),
            ),
        )
    ):
        return True
    phone_sequences = _contact_fragment_sequences(
        component_views,
        maximum_tokens=6,
        extract_numeric_tokens=False,
    )
    return _components_reconstruct_phone(phone_sequences, component_views)


class OutreachService:
    """Generate drafts without allowing the provider to decide policy."""

    def __init__(
        self,
        campaign: CampaignBrief,
        provider: DraftProvider,
        guard: DraftGuard | None = None,
    ) -> None:
        self.campaign = CampaignBrief.model_validate(campaign.model_dump(mode="json"))
        self.guard = guard or DraftGuard()
        try:
            provider_name = provider.name
        except Exception as error:
            raise ProviderConfigurationError(
                "draft provider must expose a stable non-secret name"
            ) from error
        if isinstance(provider_name, str) and type(provider_name) is not str:
            provider_name = str.__str__(provider_name)
        if type(provider_name) is not str or not _SAFE_PROVIDER_NAME.fullmatch(provider_name):
            raise ProviderConfigurationError(
                "draft provider name must be a safe identifier of at most 64 characters"
            )
        self.provider = provider
        self._provider_name = provider_name
        self._is_builtin_template_provider = type(provider) is TemplateProvider
        self._validate_campaign_boundary()

    def _validate_campaign_boundary(self) -> None:
        controlled_text = [
            self.campaign.organization_name,
            self.campaign.campaign_name,
            self.campaign.purpose,
            self.campaign.sender.name,
            self.campaign.sender.role,
            self.campaign.call_to_action.label,
            self.campaign.call_to_action.url,
        ]
        controlled_security_views = [
            *controlled_text,
            " ".join(controlled_text),
            *_pairwise_boundary_views(controlled_text),
        ]
        if any(contains_instruction_like_text(value) for value in controlled_security_views):
            raise CampaignConfigurationError(
                "campaign control fields must contain content, not model instructions"
            )
        if any(contains_policy_control_like_text(value) for value in controlled_security_views):
            raise CampaignConfigurationError(
                "campaign control fields must not contain policy-control language"
            )
        if any(
            contains_giving_history_field_like_text(value) for value in controlled_security_views
        ):
            raise CampaignConfigurationError(
                "campaign control fields must not contain raw giving-history fields"
            )
        monetary_controlled_text = controlled_text
        solicitation_controlled_text = [
            self.campaign.organization_name,
            self.campaign.campaign_name,
            self.campaign.purpose,
            self.campaign.sender.name,
            self.campaign.sender.role,
        ]
        contact_controlled_text = [
            self.campaign.organization_name,
            self.campaign.campaign_name,
            self.campaign.purpose,
            self.campaign.sender.name,
            self.campaign.sender.role,
            self.campaign.call_to_action.label,
        ]
        contact_security_views = [
            *contact_controlled_text,
            " ".join(contact_controlled_text),
            *_pairwise_boundary_views(contact_controlled_text),
        ]
        solicitation_security_views = [
            *solicitation_controlled_text,
            " ".join(solicitation_controlled_text),
            *_pairwise_boundary_views(solicitation_controlled_text),
        ]
        if any(contains_contact_like_text(value) for value in contact_security_views):
            raise CampaignConfigurationError(
                "campaign control fields must not contain contact details"
            )
        if any(
            money_expressions(value)
            for value in [
                *monetary_controlled_text,
                " ".join(monetary_controlled_text),
                *_pairwise_boundary_views(monetary_controlled_text),
            ]
        ) or any(contains_solicitation_language(value) for value in solicitation_security_views):
            raise CampaignConfigurationError(
                "campaign control fields must not override policy-owned solicitation copy"
            )

    def process_one(
        self,
        raw_record: Mapping[str, Any],
        record_index: int,
    ) -> OutreachResult:
        """Process one record and contain validation/provider failures."""

        record_snapshot: dict[str, Any] | None = None
        snapshot_donor_id: str | None = None
        try:
            record_snapshot = _snapshot_json_like_mapping(raw_record)
            snapshot_donor_id = _safe_donor_id(record_snapshot.get("donor_id"))
            structural_issue, exact_fingerprint_safe = _direct_input_structure_issue(
                record_snapshot
            )
            raw_fingerprint = (
                _fingerprint(record_snapshot, self.campaign)
                if exact_fingerprint_safe
                else _rejected_input_fingerprint(
                    record_snapshot,
                    record_index,
                    structural_issue,
                    self.campaign,
                )
            )
        except _InputSnapshotError as error:
            structural_issue = (error.code, error.message)
            raw_fingerprint = _fingerprint(
                {
                    "record_index": record_index,
                    "error": error.code,
                    "input_type": _mapping_type_tag(raw_record),
                },
                self.campaign,
            )
        except Exception:
            structural_issue = (
                "unreadable_input_mapping",
                "input mapping could not be inspected safely",
            )
            raw_fingerprint = _fingerprint(
                {
                    "record_index": record_index,
                    "error": structural_issue[0],
                    "input_type": _mapping_type_tag(raw_record),
                },
                self.campaign,
            )
        if structural_issue is not None:
            code, message = structural_issue
            return OutreachResult(
                record_index=record_index,
                donor_id=snapshot_donor_id,
                campaign_id=self.campaign.campaign_id,
                channel=None,
                status=ResultStatus.INVALID,
                review_required=True,
                reason_codes=[ReasonCode.INVALID_DONOR_RECORD],
                validation_issues=[ValidationIssue(field="$", code=code, message=message)],
                quality_issues=[],
                draft=None,
                audit=self._audit(
                    input_fingerprint=raw_fingerprint,
                    provider_called=False,
                    excluded_fact_ids=[],
                ),
            )
        assert record_snapshot is not None
        try:
            donor = DonorRecord.model_validate(record_snapshot)
        except ValidationError as error:
            return OutreachResult(
                record_index=record_index,
                donor_id=snapshot_donor_id,
                campaign_id=self.campaign.campaign_id,
                channel=None,
                status=ResultStatus.INVALID,
                review_required=True,
                reason_codes=[ReasonCode.INVALID_DONOR_RECORD],
                validation_issues=_validation_issues(error),
                quality_issues=[],
                draft=None,
                audit=self._audit(
                    input_fingerprint=raw_fingerprint,
                    provider_called=False,
                    excluded_fact_ids=[],
                ),
            )
        except Exception:
            return OutreachResult(
                record_index=record_index,
                donor_id=snapshot_donor_id,
                campaign_id=self.campaign.campaign_id,
                channel=None,
                status=ResultStatus.INVALID,
                review_required=True,
                reason_codes=[ReasonCode.INVALID_DONOR_RECORD],
                validation_issues=[
                    ValidationIssue(
                        field="$",
                        code="unreadable_input_mapping",
                        message="input mapping could not be validated safely",
                    )
                ],
                quality_issues=[],
                draft=None,
                audit=self._audit(
                    input_fingerprint=raw_fingerprint,
                    provider_called=False,
                    excluded_fact_ids=[],
                ),
            )

        normalized_fingerprint = _fingerprint(donor, self.campaign)
        decision = evaluate_policy(donor, self.campaign)
        if not decision.generation_allowed:
            status = (
                ResultStatus.SUPPRESSED
                if decision.disposition == PolicyDisposition.SUPPRESS
                else ResultStatus.BLOCKED
            )
            return self._policy_stop_result(
                donor,
                record_index,
                decision,
                status,
                normalized_fingerprint,
            )

        try:
            request = self._build_request(donor, decision)
        except (ValidationError, ValueError):
            return OutreachResult(
                record_index=record_index,
                donor_id=donor.donor_id,
                campaign_id=self.campaign.campaign_id,
                channel=donor.preferred_channel,
                status=ResultStatus.BLOCKED,
                review_required=True,
                reason_codes=[*decision.reason_codes, ReasonCode.DRAFT_REQUEST_INVALID],
                validation_issues=[],
                quality_issues=[],
                draft=None,
                audit=self._audit(
                    input_fingerprint=normalized_fingerprint,
                    provider_called=False,
                    excluded_fact_ids=decision.excluded_fact_ids,
                ),
            )
        guard_request = request.model_copy(deep=True)
        provider_request = request.model_copy(deep=True)
        try:
            provider_output = (
                TemplateProvider.generate(cast(TemplateProvider, self.provider), provider_request)
                if self._is_builtin_template_provider
                else self.provider.generate(provider_request)
            )
            candidate_payload = (
                provider_output.model_dump(mode="python")
                if isinstance(provider_output, DraftCandidate)
                else provider_output
            )
            candidate_payload = _snapshot_provider_candidate(candidate_payload)
            candidate = DraftCandidate.model_validate(candidate_payload)
        except Exception:
            return OutreachResult(
                record_index=record_index,
                donor_id=donor.donor_id,
                campaign_id=self.campaign.campaign_id,
                channel=donor.preferred_channel,
                status=ResultStatus.PROVIDER_ERROR,
                review_required=True,
                reason_codes=[
                    *decision.reason_codes,
                    ReasonCode.PROVIDER_GENERATION_FAILED,
                ],
                validation_issues=[],
                quality_issues=[],
                draft=None,
                audit=self._audit(
                    input_fingerprint=normalized_fingerprint,
                    provider_called=True,
                    excluded_fact_ids=decision.excluded_fact_ids,
                ),
            )

        quality_issues = self.guard.inspect(
            candidate,
            guard_request,
            self.campaign.prohibited_phrases,
        )
        if quality_issues:
            return OutreachResult(
                record_index=record_index,
                donor_id=donor.donor_id,
                campaign_id=self.campaign.campaign_id,
                channel=donor.preferred_channel,
                status=ResultStatus.QUALITY_REJECTED,
                review_required=True,
                reason_codes=[
                    *decision.reason_codes,
                    ReasonCode.DRAFT_FAILED_QUALITY_GATE,
                ],
                validation_issues=[],
                quality_issues=quality_issues,
                draft=None,
                audit=self._audit(
                    input_fingerprint=normalized_fingerprint,
                    provider_called=True,
                    excluded_fact_ids=decision.excluded_fact_ids,
                ),
            )

        reason_codes = list(decision.reason_codes)
        if not self._is_builtin_template_provider:
            reason_codes.append(ReasonCode.UNVERIFIED_PROVIDER_REQUIRES_REVIEW)
        result_status = (
            ResultStatus.REVIEW_REQUIRED
            if decision.disposition == PolicyDisposition.REVIEW
            or not self._is_builtin_template_provider
            else ResultStatus.DRAFT_READY
        )
        return OutreachResult(
            record_index=record_index,
            donor_id=donor.donor_id,
            campaign_id=self.campaign.campaign_id,
            channel=donor.preferred_channel,
            status=result_status,
            review_required=result_status == ResultStatus.REVIEW_REQUIRED,
            reason_codes=reason_codes,
            validation_issues=[],
            quality_issues=[],
            draft=DraftArtifact(
                subject_line=candidate.subject_line,
                body=candidate.body,
                ask=decision.ask,
                fact_ids_used=tuple(candidate.fact_ids_used),
            ),
            audit=self._audit(
                input_fingerprint=normalized_fingerprint,
                provider_called=True,
                excluded_fact_ids=decision.excluded_fact_ids,
            ),
        )

    def process_batch(
        self,
        raw_records: Sequence[Mapping[str, Any]],
    ) -> list[OutreachResult]:
        """Process every record independently in source order."""

        return [
            self.process_one(record, record_index=index)
            for index, record in enumerate(raw_records, start=1)
        ]

    def invalid_input_result(
        self,
        *,
        record_index: int,
        code: str,
        message: str,
        input_digest: str,
    ) -> OutreachResult:
        """Represent malformed JSONL without sending it to a provider."""

        return OutreachResult(
            record_index=record_index,
            donor_id=None,
            campaign_id=self.campaign.campaign_id,
            channel=None,
            status=ResultStatus.INVALID,
            review_required=True,
            reason_codes=[ReasonCode.INVALID_DONOR_RECORD],
            validation_issues=[ValidationIssue(field="$", code=code, message=message)],
            quality_issues=[],
            draft=None,
            audit=self._audit(
                input_fingerprint=_fingerprint(
                    {
                        "record_index": record_index,
                        "error": code,
                        "raw_input_sha256": input_digest,
                    },
                    self.campaign,
                ),
                provider_called=False,
                excluded_fact_ids=[],
            ),
        )

    def _build_request(
        self,
        donor: DonorRecord,
        decision: PolicyDecision,
    ) -> DraftRequest:
        if donor.title and donor.last_name:
            salutation = f"Dear {donor.title} {donor.last_name},"
        else:
            salutation = f"Hi {donor.first_name},"
        request = DraftRequest(
            channel=donor.preferred_channel,
            salutation=salutation,
            organization_name=self.campaign.organization_name,
            campaign_name=self.campaign.campaign_name,
            purpose=self.campaign.purpose,
            tone=self.campaign.tone,
            sender=self.campaign.sender,
            call_to_action=self.campaign.call_to_action,
            ask=decision.ask,
            facts=decision.eligible_facts,
        )
        co_visible_text = [
            request.salutation,
            request.organization_name,
            request.campaign_name,
            request.purpose,
            request.sender.name,
            request.sender.role,
            *(value for fact in request.facts for value in (fact.fact_id, fact.text)),
        ]
        fact_slugs = [fact.fact_id.split(".", maxsplit=1)[1] for fact in request.facts]
        normalized_fact_slugs = [
            re.sub(r"[._:-]+", " ", security_view(slug)) for slug in fact_slugs
        ]
        provider_text_components = [
            request.salutation,
            request.organization_name,
            request.campaign_name,
            request.purpose,
            request.sender.name,
            request.sender.role,
            *(
                value
                for fact, normalized_slug in zip(
                    request.facts,
                    normalized_fact_slugs,
                    strict=True,
                )
                for value in (normalized_slug, fact.text)
            ),
        ]
        cta_path_fragment = unquote(urlsplit(request.call_to_action.url).path).strip("/")
        fact_identifier_views = list(
            dict.fromkeys(
                (
                    *fact_slugs,
                    *normalized_fact_slugs,
                    " ".join(fact_slugs),
                    "-".join(fact_slugs),
                    "".join(fact_slugs),
                    " ".join(normalized_fact_slugs),
                    "-".join(normalized_fact_slugs),
                    "".join(normalized_fact_slugs),
                )
            )
        )
        co_visible_views = [
            *co_visible_text,
            *fact_identifier_views,
            " ".join(co_visible_text),
            " ".join(fact.fact_id for fact in request.facts),
            " ".join(fact.text for fact in request.facts),
        ]
        contact_text = [
            request.salutation,
            request.organization_name,
            request.campaign_name,
            request.purpose,
            request.sender.name,
            request.sender.role,
            *(fact.text for fact in request.facts),
        ]
        contact_views = [
            *contact_text,
            *fact_identifier_views,
            request.call_to_action.label,
            cta_path_fragment,
        ]

        def contact_or_identifier_unsafe(value: str) -> bool:
            return (
                contains_contact_like_text(value)
                or contains_donor_contact_value(value, donor)
                or contains_donor_identifier(value, donor.donor_id)
            )

        def control_unsafe(value: str) -> bool:
            return (
                contains_instruction_like_text(value)
                or contains_policy_control_like_text(value)
                or contains_giving_history_field_like_text(value)
            )

        if any(contact_or_identifier_unsafe(value) for value in contact_views) or any(
            control_unsafe(value)
            or contains_solicitation_language(value)
            or bool(money_expressions(value))
            for value in co_visible_views
        ):
            raise ValueError("provider-bound text failed joined-view safety checks")

        contact_components = [
            *provider_text_components,
            request.call_to_action.label,
            cta_path_fragment,
        ]
        contact_component_views = _component_security_views(contact_components)
        contact_component_forms = _component_literal_forms(contact_component_views)
        sensitive_literals = (donor.donor_id, *donor_contact_literals(donor))
        if (
            _components_complete_split_escape(contact_component_forms)
            or _components_reconstruct_control_language(contact_component_forms)
            or any(
                _components_reconstruct_literal(literal, contact_component_forms)
                for literal in sensitive_literals
            )
            or _components_reconstruct_defanged_contact(
                contact_component_views,
                contact_component_forms,
            )
        ):
            raise ValueError("provider-bound fields reconstructed protected data or controls")

        if _boundary_views_unsafe(
            _iter_pairwise_boundary_candidates(
                provider_text_components,
                _ALL_BOUNDARY_CATEGORIES,
            ),
            donor,
            _ALL_BOUNDARY_CATEGORIES,
        ):
            raise ValueError("provider-bound fields failed pairwise safety checks")

        if _boundary_views_unsafe(
            _iter_cross_boundary_candidates(
                provider_text_components,
                [request.call_to_action.label, cta_path_fragment],
                _CTA_CONTROL_BOUNDARY_CATEGORIES,
            ),
            donor,
            _CTA_CONTROL_BOUNDARY_CATEGORIES,
        ):
            raise ValueError("CTA fields failed pairwise safety checks")

        if _boundary_views_unsafe(
            _iter_cross_boundary_candidates(
                provider_text_components,
                [request.call_to_action.label, request.call_to_action.url],
                _MONEY_BOUNDARY_CATEGORY,
            ),
            donor,
            _MONEY_BOUNDARY_CATEGORY,
        ):
            raise ValueError("CTA fields failed monetary pairwise safety checks")
        return request

    def _policy_stop_result(
        self,
        donor: DonorRecord,
        record_index: int,
        decision: PolicyDecision,
        status: ResultStatus,
        input_fingerprint: str,
    ) -> OutreachResult:
        return OutreachResult(
            record_index=record_index,
            donor_id=donor.donor_id,
            campaign_id=self.campaign.campaign_id,
            channel=donor.preferred_channel,
            status=status,
            review_required=status == ResultStatus.BLOCKED,
            reason_codes=decision.reason_codes,
            validation_issues=[],
            quality_issues=[],
            draft=None,
            audit=self._audit(
                input_fingerprint=input_fingerprint,
                provider_called=False,
                excluded_fact_ids=decision.excluded_fact_ids,
            ),
        )

    def _audit(
        self,
        *,
        input_fingerprint: str,
        provider_called: bool,
        excluded_fact_ids: list[str],
    ) -> AuditMetadata:
        return AuditMetadata.model_validate(
            {
                "policy_version": POLICY_VERSION,
                "evaluated_on": self.campaign.as_of_date.isoformat(),
                "input_fingerprint": input_fingerprint,
                "provider_name": self._provider_name if provider_called else None,
                "provider_called": provider_called,
                "excluded_fact_ids": excluded_fact_ids,
            }
        )


def _validation_issues(error: ValidationError) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    error_items = error.errors(include_url=False, include_context=False, include_input=False)
    visible_items = (
        error_items
        if len(error_items) <= _MAX_VALIDATION_ISSUES
        else error_items[: _MAX_VALIDATION_ISSUES - 1]
    )
    for item in visible_items:
        location = _safe_validation_location(item["loc"])
        issues.append(
            ValidationIssue(
                field=location,
                code=str(item["type"]),
                message=_safe_diagnostic_text(str(item["msg"])),
            )
        )
    if len(error_items) > _MAX_VALIDATION_ISSUES:
        issues.append(
            ValidationIssue(
                field="$",
                code="validation_issues_truncated",
                message="additional validation issues were omitted",
            )
        )
    return issues


def _direct_input_structure_issue(
    record: Mapping[str, Any],
) -> tuple[tuple[str, str] | None, bool]:
    """Bound direct Mapping inputs before recursive third-party validation."""

    first_issue: tuple[str, str] | None = None
    scalar_units = 0
    visited_containers: set[int] = set()
    active_containers: set[int] = set()
    has_non_json_reference = False
    nodes = 0
    stack: list[tuple[str, Any, int]] = [("enter", record, 0)]

    facts = record.get("facts")
    if (
        isinstance(facts, Sequence)
        and not isinstance(facts, (str, bytes, bytearray))
        and len(facts) > _MAX_DIRECT_FACTS
    ):
        first_issue = (
            "input_collection_too_large",
            f"facts must contain at most {_MAX_DIRECT_FACTS} items",
        )

    while stack:
        operation, current, depth = stack.pop()
        if operation == "leave":
            active_containers.discard(id(current))
            visited_containers.add(id(current))
            continue

        nodes += 1
        if nodes > _MAX_DIRECT_INPUT_NODES:
            return (
                (
                    "input_structure_too_large",
                    "input structure exceeds the accepted node limit",
                ),
                False,
            )

        if isinstance(current, str | bytes | bytearray):
            scalar_units += len(current)
            if scalar_units > _MAX_DIRECT_INPUT_SCALAR_UNITS:
                return (
                    (
                        "input_content_too_large",
                        "input content exceeds the accepted size limit",
                    ),
                    False,
                )
            continue
        if isinstance(current, int) and not isinstance(current, bool):
            bit_length = int.bit_length(current)
            if bit_length > _MAX_DIRECT_INTEGER_BITS or (
                bit_length == _MAX_DIRECT_INTEGER_BITS and abs(current) >= 10**128
            ):
                first_issue = first_issue or (
                    "json_number_out_of_range",
                    "input contains an integer outside accepted limits",
                )
            continue
        if isinstance(current, float):
            if not math.isfinite(current):
                first_issue = first_issue or (
                    "non_finite_json_number",
                    "input contains a non-finite number",
                )
            continue
        if isinstance(current, Decimal):
            if Decimal.__sizeof__(current) > _MAX_DIRECT_DECIMAL_STORAGE_BYTES:
                first_issue = first_issue or (
                    "json_number_out_of_range",
                    "input contains a decimal outside accepted limits",
                )
                continue
            decimal_tuple = current.as_tuple()
            exponent = decimal_tuple.exponent
            if (
                not current.is_finite()
                or len(decimal_tuple.digits) > 128
                or not isinstance(exponent, int)
                or abs(exponent) > 100
                or (current and abs(current.adjusted()) > 100)
            ):
                first_issue = first_issue or (
                    "json_number_out_of_range",
                    "input contains a decimal outside accepted limits",
                )
            continue

        is_mapping = isinstance(current, Mapping)
        is_sequence = isinstance(current, Sequence) and not isinstance(
            current, (str, bytes, bytearray)
        )
        if not is_mapping and not is_sequence:
            continue

        container_id = id(current)
        if container_id in active_containers:
            first_issue = first_issue or (
                "input_cycle_not_allowed",
                "input must be an acyclic JSON-like structure",
            )
            has_non_json_reference = True
            continue
        if container_id in visited_containers:
            first_issue = first_issue or (
                "input_shared_reference_not_allowed",
                "input must not reuse mutable container references",
            )
            has_non_json_reference = True
            continue
        if depth >= _MAX_DIRECT_INPUT_DEPTH:
            first_issue = first_issue or (
                "input_nesting_too_deep",
                "input contains excessive nesting",
            )
        if len(current) > _MAX_DIRECT_CONTAINER_ITEMS:
            return (
                (
                    "input_collection_too_large",
                    "input contains a collection outside accepted limits",
                ),
                False,
            )

        active_containers.add(container_id)
        stack.append(("leave", current, depth))
        children = (
            (child for pair in cast(Mapping[Any, Any], current).items() for child in pair)
            if is_mapping
            else iter(cast(Sequence[Any], current))
        )
        stack.extend(("enter", child, depth + 1) for child in children)

    return first_issue, first_issue is None and not has_non_json_reference


def _snapshot_json_like_mapping(record: Mapping[str, Any]) -> dict[str, Any]:
    """Materialize one bounded plain-tree snapshot to close Mapping TOCTOU behavior."""

    active_containers: set[int] = set()
    visited_containers: set[int] = set()
    nodes = 0
    scalar_units = 0

    def clone(
        current: Any,
        depth: int,
        collection_limit: int = _MAX_DIRECT_CONTAINER_ITEMS,
    ) -> Any:
        nonlocal nodes, scalar_units
        nodes += 1
        if nodes > _MAX_DIRECT_INPUT_NODES:
            raise _InputSnapshotError(
                "input_structure_too_large",
                "input structure exceeds the accepted node limit",
            )
        if isinstance(current, str | bytes | bytearray):
            if isinstance(current, str):
                scalar_length = str.__len__(current)
                if scalar_length > _MAX_DIRECT_INPUT_SCALAR_UNITS - scalar_units:
                    raise _InputSnapshotError(
                        "input_content_too_large",
                        "input content exceeds the accepted size limit",
                    )
                cloned_scalar: str | bytes = (
                    current if type(current) is str else str.__str__(current)
                )
            elif isinstance(current, bytes):
                scalar_length = bytes.__len__(current)
                if scalar_length > _MAX_DIRECT_INPUT_SCALAR_UNITS - scalar_units:
                    raise _InputSnapshotError(
                        "input_content_too_large",
                        "input content exceeds the accepted size limit",
                    )
                cloned_scalar = current if type(current) is bytes else bytes.__bytes__(current)
            else:
                scalar_length = bytearray.__len__(current)
                remaining_units = _MAX_DIRECT_INPUT_SCALAR_UNITS - scalar_units
                if scalar_length > remaining_units:
                    raise _InputSnapshotError(
                        "input_content_too_large",
                        "input content exceeds the accepted size limit",
                    )
                base_bytearray = bytearray.__getitem__(
                    current,
                    slice(0, remaining_units + 1),
                )
                scalar_length = bytearray.__len__(base_bytearray)
                if scalar_length > remaining_units:
                    raise _InputSnapshotError(
                        "input_content_too_large",
                        "input content exceeds the accepted size limit",
                    )
                cloned_scalar = bytes(base_bytearray)
            scalar_units += scalar_length
            if scalar_units > _MAX_DIRECT_INPUT_SCALAR_UNITS:
                raise _InputSnapshotError(
                    "input_content_too_large",
                    "input content exceeds the accepted size limit",
                )
            return cloned_scalar
        if current is None:
            return None
        if isinstance(current, bool):
            return current
        if isinstance(current, int):
            if int.bit_length(current) > _MAX_DIRECT_INTEGER_BITS:
                raise _InputSnapshotError(
                    "json_number_out_of_range",
                    "input contains an integer outside accepted limits",
                )
            return current if type(current) is int else int.__index__(current)
        if isinstance(current, float):
            return current if type(current) is float else float.__float__(current)
        if isinstance(current, Decimal):
            if Decimal.__sizeof__(current) > _MAX_DIRECT_DECIMAL_STORAGE_BYTES:
                raise _InputSnapshotError(
                    "json_number_out_of_range",
                    "input contains a decimal outside accepted limits",
                )
            return current if type(current) is Decimal else Decimal(current)

        is_mapping = isinstance(current, Mapping)
        is_sequence = isinstance(current, Sequence) and not isinstance(
            current, (str, bytes, bytearray)
        )
        if not is_mapping and not is_sequence:
            raise _InputSnapshotError(
                "non_json_input_value",
                "input contains a non-JSON scalar value",
            )
        if depth >= _MAX_DIRECT_INPUT_DEPTH:
            raise _InputSnapshotError(
                "input_nesting_too_deep",
                "input contains excessive nesting",
            )

        container_id = id(current)
        if container_id in active_containers:
            raise _InputSnapshotError(
                "input_cycle_not_allowed",
                "input must be an acyclic JSON-like structure",
            )
        if container_id in visited_containers:
            raise _InputSnapshotError(
                "input_shared_reference_not_allowed",
                "input must not reuse mutable container references",
            )
        active_containers.add(container_id)
        try:
            if is_mapping:
                result: dict[str, Any] = {}
                for count, (key, item) in enumerate(
                    cast(Mapping[Any, Any], current).items(), start=1
                ):
                    if count > collection_limit:
                        raise _InputSnapshotError(
                            "input_collection_too_large",
                            "input mapping yielded too many items",
                        )
                    cloned_key = clone(key, depth + 1)
                    if type(cloned_key) is not str:
                        raise _InputSnapshotError(
                            "non_string_input_key",
                            "input mapping keys must be strings",
                        )
                    if cloned_key in result:
                        raise _InputSnapshotError(
                            "duplicate_input_key",
                            "input mapping yielded a duplicate key",
                        )
                    item_limit = (
                        _MAX_DIRECT_FACTS
                        if depth == 0 and cloned_key in {"facts", "fact_ids_used"}
                        else _MAX_DIRECT_CONTAINER_ITEMS
                    )
                    cloned_item = clone(item, depth + 1, item_limit)
                    result[cloned_key] = cloned_item
                return result

            bounded_items: list[Any] = []
            for count, item in enumerate(cast(Sequence[Any], current), start=1):
                if count > collection_limit:
                    raise _InputSnapshotError(
                        "input_collection_too_large",
                        "input sequence yielded too many items",
                    )
                bounded_items.append(item)
            return [clone(item, depth + 1) for item in bounded_items]
        finally:
            active_containers.discard(container_id)
            visited_containers.add(container_id)

    snapshot = clone(record, 0)
    if not isinstance(snapshot, dict):
        raise ValueError("input root must materialize as an object")
    return cast(dict[str, Any], snapshot)


def _snapshot_provider_candidate(value: Any) -> Any:
    """Bound and detach a provider Mapping before contract validation."""

    if not isinstance(value, Mapping):
        return value
    snapshot = _snapshot_json_like_mapping(cast(Mapping[str, Any], value))
    if len(snapshot) > 8:
        raise ValueError("provider candidate has too many fields")
    field_limits = {
        "subject_line": 200,
        "salutation": 330,
        "body": 6_000,
    }
    for field, maximum in field_limits.items():
        field_value = snapshot.get(field)
        if isinstance(field_value, str) and len(field_value) > maximum:
            raise ValueError("provider candidate text exceeds its field limit")
    fact_ids = snapshot.get("fact_ids_used")
    if (
        isinstance(fact_ids, Sequence)
        and not isinstance(fact_ids, (str, bytes, bytearray))
        and len(fact_ids) > 25
    ):
        raise ValueError("provider candidate references too many facts")
    structural_issue, _ = _direct_input_structure_issue(snapshot)
    if structural_issue is not None:
        raise ValueError("provider candidate exceeds the accepted structural limits")
    return snapshot


def _rejected_input_fingerprint(
    record: Mapping[str, Any],
    record_index: int,
    issue: tuple[str, str] | None,
    campaign: CampaignBrief,
) -> str:
    """Fingerprint a bounded, non-sensitive summary of an oversized direct input."""

    facts = record.get("facts")
    summary = {
        "record_index": record_index,
        "error": issue[0] if issue is not None else "input_structure_too_large",
        "input_type": _mapping_type_tag(record),
        "top_level_items": len(record),
        "facts_items": (
            len(facts)
            if isinstance(facts, Sequence) and not isinstance(facts, (str, bytes, bytearray))
            else None
        ),
        "donor_id": _safe_donor_id(record.get("donor_id")),
    }
    return _fingerprint(summary, campaign)


def _safe_validation_location(parts: tuple[int | str, ...]) -> str:
    sanitized = [
        str(part)
        if isinstance(part, int) and part >= 0
        else part
        if isinstance(part, str) and part in _DECLARED_DONOR_LOCATION_FIELDS
        else "$extra"
        for part in parts
    ]
    return ".".join(sanitized) or "$"


def _safe_donor_id(value: object) -> str | None:
    if isinstance(value, str) and _SAFE_DONOR_ID.fullmatch(value):
        return value
    return None


def _mapping_type_tag(value: object) -> str:
    """Return a bounded type tag without reading attacker-controlled class metadata."""

    return "builtins.dict" if type(value) is dict else "custom_mapping"


def _safe_diagnostic_text(value: str, *, max_length: int = 320) -> str:
    encoded = value.encode("utf-8", errors="backslashreplace").decode("utf-8")
    sanitized = "".join(
        character if not is_unsafe_text_character(character) else f"\\u{ord(character):04x}"
        for character in encoded
    )
    result = sanitized[:max_length].strip()
    return result or "validation failed"


def _fingerprint(
    record: Mapping[str, Any] | DonorRecord,
    campaign: CampaignBrief,
) -> str:
    if isinstance(record, DonorRecord):
        record_payload: Any = record.model_dump(mode="json")
    else:
        record_payload = record
    payload = {
        "campaign": campaign.model_dump(mode="json"),
        "record": record_payload,
    }
    return _iterative_fingerprint(payload)


def _fingerprint_scalar_token(value: Any) -> list[Any] | None:
    if value is None:
        return ["null"]
    if isinstance(value, bool):
        return ["boolean", value]
    if isinstance(value, str):
        return ["string", value]
    if isinstance(value, int):
        return _integer_fingerprint_token(value)
    if isinstance(value, Decimal):
        decimal_tuple = value.as_tuple()
        exponent = decimal_tuple.exponent
        return [
            "decimal",
            decimal_tuple.sign,
            len(decimal_tuple.digits),
            hashlib.sha256(bytes(decimal_tuple.digits)).hexdigest(),
            (
                _integer_fingerprint_token(exponent)
                if isinstance(exponent, int)
                else ["special_exponent", exponent]
            ),
        ]
    if isinstance(value, float):
        return ["float", value.hex()]
    if isinstance(value, date):
        return ["date", value.isoformat()]
    if isinstance(value, (bytes, bytearray)):
        return ["bytes", hashlib.sha256(bytes(value)).hexdigest()]
    if isinstance(value, Mapping):
        return None
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return None
    return ["unsupported"]


def _integer_fingerprint_token(value: int) -> list[Any]:
    """Encode any-size integers without Python's decimal conversion limit."""

    magnitude = abs(value)
    byte_length = max(1, (magnitude.bit_length() + 7) // 8)
    encoded = magnitude.to_bytes(byte_length, "big")
    return [
        "integer",
        -1 if value < 0 else 1,
        magnitude.bit_length(),
        hashlib.sha256(encoded).hexdigest(),
    ]


def _iterative_fingerprint(value: Any) -> str:
    """Hash type-tagged JSON-like data without recursion or depth sentinels."""

    digest = hashlib.sha256()
    stack: list[tuple[str, Any]] = [("value", value)]
    active_containers: set[int] = set()

    def emit(token: list[Any]) -> None:
        encoded = json.dumps(token, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)

    while stack:
        operation, current = stack.pop()
        if operation == "token":
            emit(current)
            continue
        if operation == "leave":
            active_containers.discard(current)
            continue

        scalar = _fingerprint_scalar_token(current)
        if scalar is not None:
            emit(scalar)
            continue

        container_id = id(current)
        if container_id in active_containers:
            emit(["cycle"])
            continue
        active_containers.add(container_id)
        stack.append(("leave", container_id))

        if isinstance(current, Mapping):
            items = list(current.items())
            items.sort(
                key=lambda pair: json.dumps(
                    _fingerprint_scalar_token(pair[0]) or ["unsupported_key"],
                    separators=(",", ":"),
                    ensure_ascii=True,
                )
            )
            emit(["object_start", len(items)])
            stack.append(("token", ["object_end"]))
            for key, item in reversed(items):
                key_token = _fingerprint_scalar_token(key) or ["unsupported_key"]
                stack.append(("value", item))
                stack.append(("token", ["key", key_token]))
            continue

        assert isinstance(current, Sequence)
        emit(["array_start", len(current)])
        stack.append(("token", ["array_end"]))
        for item in reversed(current):
            stack.append(("value", item))

    return digest.hexdigest()
