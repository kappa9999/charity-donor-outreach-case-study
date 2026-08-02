"""JSON/JSONL input handling and atomic result output."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from types import TracebackType
from typing import Any, Literal, Self, TextIO, cast

from .models import CampaignBrief, OutreachResult, contains_invalid_unicode_scalar


@dataclass(frozen=True, slots=True)
class JsonLine:
    line_number: int
    input_digest: str
    value: Mapping[str, Any] | None
    error_code: str | None
    error_message: str | None


class StrictJsonError(ValueError):
    """Base error for JSON values outside the accepted interoperable profile."""


class DuplicateJsonKeyError(StrictJsonError):
    """Raised when an object repeats a key at any nesting level."""


class NonFiniteJsonNumberError(StrictJsonError):
    """Raised for NaN and infinity, which are not valid standard JSON numbers."""


class InvalidUnicodeJsonError(StrictJsonError):
    """Raised when decoded JSON contains an invalid Unicode scalar value."""


class JsonNumberRangeError(StrictJsonError):
    """Raised before constructing an unreasonably large JSON number."""


class JsonNestingDepthError(StrictJsonError):
    """Raised when a JSON container exceeds the accepted nesting depth."""


class InputSizeError(StrictJsonError):
    """Raised before an input file or record can consume unbounded memory."""


_MAX_JSON_NUMBER_CHARACTERS = 128
_MAX_JSON_DECIMAL_EXPONENT = 100
_MAX_JSON_NESTING_DEPTH = 64
MAX_JSONL_LINE_BYTES = 1_048_576
MAX_CAMPAIGN_FILE_BYTES = 1_048_576
_MAX_CAMPAIGN_STRUCTURE_NODES = 10_000
_MAX_CAMPAIGN_COLLECTION_ITEMS = 1_000


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateJsonKeyError("JSON objects must not contain duplicate keys")
        result[key] = value
    return result


def _reject_non_finite_number(value: str) -> None:
    raise NonFiniteJsonNumberError(f"non-finite JSON number is not allowed: {value}")


def _parse_bounded_integer(value: str) -> int:
    if len(value.lstrip("-")) > _MAX_JSON_NUMBER_CHARACTERS:
        raise JsonNumberRangeError("JSON integer exceeds the accepted size limit")
    return int(value)


def _parse_bounded_decimal(value: str) -> Decimal:
    if len(value) > _MAX_JSON_NUMBER_CHARACTERS:
        raise JsonNumberRangeError("JSON decimal exceeds the accepted size limit")
    try:
        number = Decimal(value)
    except InvalidOperation as error:
        raise JsonNumberRangeError("JSON decimal is invalid") from error
    exponent = number.as_tuple().exponent
    if (
        not number.is_finite()
        or (isinstance(exponent, int) and abs(exponent) > _MAX_JSON_DECIMAL_EXPONENT)
        or (number and abs(number.adjusted()) > _MAX_JSON_DECIMAL_EXPONENT)
    ):
        raise JsonNumberRangeError("JSON decimal exponent exceeds the accepted limit")
    return number


def loads_json_strict(text: str) -> Any:
    """Parse standard JSON while rejecting ambiguous duplicate object keys."""

    payload = json.loads(
        text,
        object_pairs_hook=_reject_duplicate_keys,
        parse_constant=_reject_non_finite_number,
        parse_int=_parse_bounded_integer,
        parse_float=_parse_bounded_decimal,
    )
    if _exceeds_json_nesting_depth(payload):
        raise JsonNestingDepthError("JSON value exceeds the accepted nesting depth")
    if contains_invalid_unicode_scalar(payload):
        raise InvalidUnicodeJsonError("JSON text contains an invalid Unicode scalar value")
    return payload


def _exceeds_json_nesting_depth(value: Any) -> bool:
    stack: list[tuple[Any, int]] = [(value, 0)]
    while stack:
        current, depth = stack.pop()
        if isinstance(current, Mapping):
            if depth >= _MAX_JSON_NESTING_DEPTH:
                return True
            stack.extend((item, depth + 1) for item in current.values())
        elif isinstance(current, list):
            if depth >= _MAX_JSON_NESTING_DEPTH:
                return True
            stack.extend((item, depth + 1) for item in current)
    return False


def load_campaign(path: Path) -> CampaignBrief:
    with path.open("rb") as handle:
        raw_campaign = handle.read(MAX_CAMPAIGN_FILE_BYTES + 1)
    if len(raw_campaign) > MAX_CAMPAIGN_FILE_BYTES:
        raise InputSizeError("campaign file exceeds the accepted size limit")
    payload = loads_json_strict(raw_campaign.decode("utf-8-sig"))
    if _campaign_payload_exceeds_limits(payload):
        raise InputSizeError("campaign structure exceeds the accepted size limits")
    return CampaignBrief.model_validate(payload)


def _campaign_payload_exceeds_limits(payload: Any) -> bool:
    if not isinstance(payload, Mapping):
        return False
    for field, maximum in (("facts", 50), ("prohibited_phrases", 50)):
        value = payload.get(field)
        if isinstance(value, list) and len(value) > maximum:
            return True
    review_policy = payload.get("review_policy")
    if isinstance(review_policy, Mapping):
        segments = review_policy.get("segments")
        if isinstance(segments, list) and len(segments) > 5:
            return True

    nodes = 0
    stack = [payload]
    while stack:
        current = stack.pop()
        nodes += 1
        if nodes > _MAX_CAMPAIGN_STRUCTURE_NODES:
            return True
        if isinstance(current, Mapping):
            if len(current) > _MAX_CAMPAIGN_COLLECTION_ITEMS:
                return True
            stack.extend(current.values())
        elif isinstance(current, list):
            if len(current) > _MAX_CAMPAIGN_COLLECTION_ITEMS:
                return True
            stack.extend(current)
    return False


def iter_jsonl(path: Path) -> Iterator[JsonLine]:
    with path.open("rb") as handle:
        line_number = 0
        while True:
            raw_line = handle.readline(MAX_JSONL_LINE_BYTES + 1)
            if not raw_line:
                break
            line_number += 1
            input_hasher = hashlib.sha256(raw_line)
            oversized = len(raw_line) > MAX_JSONL_LINE_BYTES
            while not raw_line.endswith(b"\n"):
                continuation = handle.readline(MAX_JSONL_LINE_BYTES + 1)
                if not continuation:
                    break
                input_hasher.update(continuation)
                oversized = True
                if continuation.endswith(b"\n"):
                    break
            input_digest = input_hasher.hexdigest()
            if oversized:
                yield JsonLine(
                    line_number=line_number,
                    input_digest=input_digest,
                    value=None,
                    error_code="input_line_too_large",
                    error_message=(f"line exceeds the {MAX_JSONL_LINE_BYTES}-byte input limit"),
                )
                continue
            try:
                line = raw_line.decode("utf-8-sig" if line_number == 1 else "utf-8")
            except UnicodeDecodeError:
                yield JsonLine(
                    line_number=line_number,
                    input_digest=input_digest,
                    value=None,
                    error_code="invalid_utf8",
                    error_message="line is not valid UTF-8 text",
                )
                continue
            if not line.strip():
                continue
            try:
                payload = loads_json_strict(line)
            except DuplicateJsonKeyError:
                yield JsonLine(
                    line_number=line_number,
                    input_digest=input_digest,
                    value=None,
                    error_code="duplicate_json_key",
                    error_message="line contains a duplicate object key",
                )
                continue
            except NonFiniteJsonNumberError:
                yield JsonLine(
                    line_number=line_number,
                    input_digest=input_digest,
                    value=None,
                    error_code="non_finite_json_number",
                    error_message="line contains a non-finite JSON number",
                )
                continue
            except InvalidUnicodeJsonError:
                yield JsonLine(
                    line_number=line_number,
                    input_digest=input_digest,
                    value=None,
                    error_code="invalid_unicode_scalar",
                    error_message="line contains an invalid Unicode scalar value",
                )
                continue
            except JsonNumberRangeError:
                yield JsonLine(
                    line_number=line_number,
                    input_digest=input_digest,
                    value=None,
                    error_code="json_number_out_of_range",
                    error_message="line contains a JSON number outside accepted limits",
                )
                continue
            except JsonNestingDepthError:
                yield JsonLine(
                    line_number=line_number,
                    input_digest=input_digest,
                    value=None,
                    error_code="json_nesting_too_deep",
                    error_message="line contains excessive JSON nesting",
                )
                continue
            except json.JSONDecodeError:
                yield JsonLine(
                    line_number=line_number,
                    input_digest=input_digest,
                    value=None,
                    error_code="invalid_json",
                    error_message="line is not valid JSON",
                )
                continue
            except RecursionError:
                yield JsonLine(
                    line_number=line_number,
                    input_digest=input_digest,
                    value=None,
                    error_code="json_nesting_too_deep",
                    error_message="line contains excessive JSON nesting",
                )
                continue
            if not isinstance(payload, dict):
                yield JsonLine(
                    line_number=line_number,
                    input_digest=input_digest,
                    value=None,
                    error_code="object_required",
                    error_message="each JSONL line must contain one object",
                )
                continue
            yield JsonLine(
                line_number=line_number,
                input_digest=input_digest,
                value=payload,
                error_code=None,
                error_message=None,
            )


def paths_alias(first: Path, second: Path) -> bool:
    """Return true for equal, symlinked, or hard-linked filesystem paths."""

    if first.resolve(strict=False) == second.resolve(strict=False):
        return True
    try:
        return os.path.samefile(first, second)
    except FileNotFoundError:
        return False


class AtomicJsonlWriter:
    """Stream results to a temporary file and replace the target on success."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._handle: TextIO | None = None
        self._temporary_path: Path | None = None

    def __enter__(self) -> Self:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=self.path.parent,
            prefix=f".{self.path.name}.",
            suffix=".tmp",
            delete=False,
        )
        self._handle = cast(TextIO, handle)
        self._temporary_path = Path(handle.name)
        return self

    def write(self, result: OutreachResult) -> None:
        if self._handle is None:
            raise RuntimeError("atomic writer is not open")
        self._handle.write(result.model_dump_json())
        self._handle.write("\n")

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> Literal[False]:
        del exc_value, traceback
        handle = self._handle
        temporary_path = self._temporary_path
        try:
            if handle is not None:
                try:
                    if exc_type is None:
                        handle.flush()
                        os.fsync(handle.fileno())
                finally:
                    handle.close()
            if exc_type is None and temporary_path is not None:
                os.replace(temporary_path, self.path)
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()
            self._handle = None
            self._temporary_path = None
        return False
