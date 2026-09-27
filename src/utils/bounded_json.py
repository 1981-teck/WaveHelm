from __future__ import annotations

"""Strict, bounded JSON parsing and serialization helpers.

The helpers enforce document byte limits before parsing and validate the entire
JSON tree, including values that a downstream schema may later ignore.

Complexity: O(N + B), where N is the number of JSON nodes and B is the encoded
payload size. Memory use is O(N + B), bounded by the supplied limits.
"""

from collections.abc import Mapping
from dataclasses import dataclass
import json
import math
from pathlib import Path
from typing import Literal, TypeAlias, cast

JsonScalar: TypeAlias = None | bool | int | float | str
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
JsonRoot: TypeAlias = Literal["any", "object", "array"]
MAX_JSON_INDENT = 8


class BoundedJsonError(ValueError):
    """Raised when a JSON document violates syntax, type, or resource limits."""


@dataclass(frozen=True, slots=True)
class JsonLimits:
    """Hard resource limits for one JSON document or in-memory JSON value."""

    max_bytes: int
    max_depth: int
    max_nodes: int
    max_container_items: int
    max_key_chars: int
    max_key_bytes: int
    max_string_chars: int
    max_string_bytes: int
    max_total_text_chars: int
    max_total_text_bytes: int
    max_number_chars: int = 128

    def __post_init__(self) -> None:
        values = (
            self.max_bytes,
            self.max_depth,
            self.max_nodes,
            self.max_container_items,
            self.max_key_chars,
            self.max_key_bytes,
            self.max_string_chars,
            self.max_string_bytes,
            self.max_total_text_chars,
            self.max_total_text_bytes,
            self.max_number_chars,
        )
        if any(type(value) is not int or value < 1 for value in values):
            raise ValueError("JSON limits must be positive integers")


@dataclass(slots=True)
class _Budget:
    limits: JsonLimits
    nodes: int = 0
    text_chars: int = 0
    text_bytes: int = 0

    def consume_node(self) -> None:
        self.nodes += 1
        if self.nodes > self.limits.max_nodes:
            raise BoundedJsonError("JSON node limit exceeded")

    def consume_text(self, value: str, *, key: bool) -> None:
        char_limit = self.limits.max_key_chars if key else self.limits.max_string_chars
        byte_limit = self.limits.max_key_bytes if key else self.limits.max_string_bytes
        label = "key" if key else "string"
        if len(value) > char_limit:
            raise BoundedJsonError(f"JSON {label} character limit exceeded")
        try:
            encoded = value.encode("utf-8")
        except UnicodeEncodeError as error:
            raise BoundedJsonError(f"JSON {label} is not valid UTF-8 text") from error
        if len(encoded) > byte_limit:
            raise BoundedJsonError(f"JSON {label} UTF-8 byte limit exceeded")
        self.text_chars += len(value)
        self.text_bytes += len(encoded)
        if self.text_chars > self.limits.max_total_text_chars:
            raise BoundedJsonError("JSON total text character budget exceeded")
        if self.text_bytes > self.limits.max_total_text_bytes:
            raise BoundedJsonError("JSON total text byte budget exceeded")


def read_json_file(
    path: Path,
    *,
    limits: JsonLimits,
    root: JsonRoot = "any",
) -> JsonValue:
    """Read and parse one UTF-8 JSON file without allocating beyond the byte cap.

    Edge cases:
        1. Oversized files fail after at most ``max_bytes + 1`` bytes are read.
        2. Invalid UTF-8, duplicate keys, and non-finite numbers fail closed.
        3. The entire parsed tree is budgeted before downstream schema handling.
    """
    with Path(path).open("rb") as handle:
        payload = handle.read(limits.max_bytes + 1)
    return parse_json_bytes(payload, limits=limits, root=root)


def parse_json_bytes(
    payload: bytes,
    *,
    limits: JsonLimits,
    root: JsonRoot = "any",
) -> JsonValue:
    """Parse bounded UTF-8 JSON bytes and return a detached strict JSON tree."""
    if len(payload) > limits.max_bytes:
        raise BoundedJsonError("JSON document byte limit exceeded")
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise BoundedJsonError("JSON document is not valid UTF-8") from error
    return _parse_json_text(text, limits=limits, root=root)


def parse_json_text(
    text: str,
    *,
    limits: JsonLimits,
    root: JsonRoot = "any",
) -> JsonValue:
    """Parse strict JSON text and validate its complete resource budget."""
    if type(text) is not str:
        raise BoundedJsonError("JSON input must be text")
    _validate_text_byte_size(text, limits)
    return _parse_json_text(text, limits=limits, root=root)


def _parse_json_text(text: str, *, limits: JsonLimits, root: JsonRoot) -> JsonValue:
    try:
        parsed = json.loads(
            text,
            object_pairs_hook=lambda pairs: _build_object(pairs, limits),
            parse_constant=_reject_constant,
            parse_int=lambda token: _parse_integer(token, limits),
            parse_float=lambda token: _parse_float(token, limits),
        )
    except BoundedJsonError:
        raise
    except json.JSONDecodeError as error:
        raise BoundedJsonError(
            f"invalid JSON at line {error.lineno}, column {error.colno}: {error.msg}"
        ) from error
    except RecursionError as error:
        raise BoundedJsonError("JSON parser nesting limit exceeded") from error
    return normalize_json_value(parsed, limits=limits, root=root)


def normalize_json_value(
    value: object,
    *,
    limits: JsonLimits,
    root: JsonRoot = "any",
) -> JsonValue:
    """Validate and deep-copy a strict in-memory JSON value within hard limits."""
    budget = _Budget(limits)
    normalized = _normalize_node(value, depth=0, budget=budget, active=set())
    _validate_root(normalized, root)
    return normalized


def normalize_json_mapping(
    value: object,
    *,
    limits: JsonLimits,
) -> dict[str, JsonValue]:
    """Copy a caller-provided mapping without trusting its reported length.

    Edge cases:
        1. Infinite or dishonest iterators stop after the configured item cap.
        2. Duplicate or non-string keys fail before strict JSON normalization.
        3. Provider exceptions are converted at this explicit dynamic boundary.
    """
    if not isinstance(value, Mapping):
        raise BoundedJsonError("JSON mapping input must implement Mapping")
    candidate: dict[str, object] = {}
    try:
        for index, (raw_key, item) in enumerate(value.items()):
            if index >= limits.max_container_items:
                raise BoundedJsonError("JSON mapping item limit exceeded")
            if type(raw_key) is not str:
                raise BoundedJsonError("JSON object keys must be strings")
            if raw_key in candidate:
                raise BoundedJsonError(f"Duplicate JSON key: {raw_key!r}")
            candidate[raw_key] = item
    except BoundedJsonError:
        raise
    except Exception as error:  # explicit caller-provided Mapping boundary
        raise BoundedJsonError(
            f"unable to read JSON mapping input: {type(error).__name__}: {error}"
        ) from error
    normalized = normalize_json_value(candidate, limits=limits, root="object")
    return cast(dict[str, JsonValue], normalized)


def serialize_json_bytes(
    value: object,
    *,
    limits: JsonLimits,
    root: JsonRoot = "any",
    indent: int | None = None,
    sort_keys: bool = False,
    trailing_newline: bool = False,
) -> tuple[JsonValue, bytes]:
    """Normalize and serialize one JSON value without exceeding its byte budget."""
    _validate_serialization_options(indent, sort_keys, trailing_newline)
    normalized = normalize_json_value(value, limits=limits, root=root)
    try:
        text = json.dumps(
            normalized,
            ensure_ascii=False,
            indent=indent,
            allow_nan=False,
            sort_keys=sort_keys,
        )
        if trailing_newline:
            text += "\n"
        payload = text.encode("utf-8")
    except (RecursionError, TypeError, UnicodeError, ValueError) as error:
        raise BoundedJsonError(f"unable to serialize JSON: {error}") from error
    if len(payload) > limits.max_bytes:
        raise BoundedJsonError("serialized JSON document exceeds the byte limit")
    return normalized, payload



def _validate_serialization_options(
    indent: int | None, sort_keys: bool, trailing_newline: bool
) -> None:
    if indent is not None and (type(indent) is not int or not 0 <= indent <= MAX_JSON_INDENT):
        raise BoundedJsonError(f"JSON indent must be between 0 and {MAX_JSON_INDENT}")
    if type(sort_keys) is not bool:
        raise BoundedJsonError("JSON sort_keys option must be a boolean")
    if type(trailing_newline) is not bool:
        raise BoundedJsonError("JSON trailing_newline option must be a boolean")

def _validate_text_byte_size(text: str, limits: JsonLimits) -> None:
    try:
        encoded_size = len(text.encode("utf-8"))
    except UnicodeEncodeError as error:
        raise BoundedJsonError("JSON document is not valid UTF-8 text") from error
    if encoded_size > limits.max_bytes:
        raise BoundedJsonError("JSON document byte limit exceeded")


def _build_object(
    pairs: list[tuple[str, object]], limits: JsonLimits
) -> dict[str, object]:
    if len(pairs) > limits.max_container_items:
        raise BoundedJsonError("JSON object item limit exceeded")
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise BoundedJsonError(f"Duplicate JSON key: {key!r}")
        result[key] = value
    return result


def _reject_constant(value: str) -> object:
    raise BoundedJsonError(f"Non-standard JSON numeric constant: {value}")


def _parse_integer(token: str, limits: JsonLimits) -> int:
    if len(token) > limits.max_number_chars:
        raise BoundedJsonError("JSON integer token limit exceeded")
    try:
        return int(token)
    except ValueError as error:
        raise BoundedJsonError("invalid JSON integer") from error


def _parse_float(token: str, limits: JsonLimits) -> float:
    if len(token) > limits.max_number_chars:
        raise BoundedJsonError("JSON number token limit exceeded")
    try:
        value = float(token)
    except ValueError as error:
        raise BoundedJsonError("invalid JSON number") from error
    if not math.isfinite(value):
        raise BoundedJsonError("JSON number must be finite")
    return value


def _normalize_node(
    value: object,
    *,
    depth: int,
    budget: _Budget,
    active: set[int],
) -> JsonValue:
    if depth > budget.limits.max_depth:
        raise BoundedJsonError("JSON depth limit exceeded")
    budget.consume_node()
    if value is None or type(value) is bool:
        return cast(JsonScalar, value)
    if type(value) is int:
        _validate_integer_value(cast(int, value), budget.limits)
        return cast(int, value)
    if type(value) is float:
        if not math.isfinite(cast(float, value)):
            raise BoundedJsonError("JSON number must be finite")
        return cast(float, value)
    if type(value) is str:
        budget.consume_text(cast(str, value), key=False)
        return cast(str, value)
    if type(value) is list:
        return _normalize_list(cast(list[object], value), depth, budget, active)
    if type(value) is dict:
        return _normalize_object(cast(dict[object, object], value), depth, budget, active)
    raise BoundedJsonError("value is not part of the strict JSON data model")


def _validate_integer_value(value: int, limits: JsonLimits) -> None:
    if value.bit_length() > limits.max_number_chars * 4:
        raise BoundedJsonError("JSON integer token limit exceeded")
    if len(str(abs(value))) > limits.max_number_chars:
        raise BoundedJsonError("JSON integer token limit exceeded")


def _normalize_list(
    values: list[object], depth: int, budget: _Budget, active: set[int]
) -> list[JsonValue]:
    _validate_container(values, budget, active)
    try:
        return [
            _normalize_node(item, depth=depth + 1, budget=budget, active=active)
            for item in values
        ]
    finally:
        active.remove(id(values))


def _normalize_object(
    values: dict[object, object], depth: int, budget: _Budget, active: set[int]
) -> dict[str, JsonValue]:
    _validate_container(values, budget, active)
    result: dict[str, JsonValue] = {}
    try:
        for raw_key, item in values.items():
            if type(raw_key) is not str:
                raise BoundedJsonError("JSON object keys must be strings")
            key = cast(str, raw_key)
            budget.consume_node()
            budget.consume_text(key, key=True)
            result[key] = _normalize_node(
                item, depth=depth + 1, budget=budget, active=active
            )
        return result
    finally:
        active.remove(id(values))


def _validate_container(
    values: list[object] | dict[object, object],
    budget: _Budget,
    active: set[int],
) -> None:
    if len(values) > budget.limits.max_container_items:
        raise BoundedJsonError("JSON container item limit exceeded")
    identity = id(values)
    if identity in active:
        raise BoundedJsonError("cyclic JSON value is not allowed")
    active.add(identity)


def _validate_root(value: JsonValue, root: JsonRoot) -> None:
    if root == "any":
        return
    if root == "object":
        if type(value) is not dict:
            raise BoundedJsonError("JSON document root must be an object")
        return
    if root == "array":
        if type(value) is not list:
            raise BoundedJsonError("JSON document root must be an array")
        return
    raise BoundedJsonError("unsupported JSON root contract")


__all__ = [
    "BoundedJsonError",
    "JsonLimits",
    "JsonRoot",
    "JsonScalar",
    "JsonValue",
    "normalize_json_mapping",
    "normalize_json_value",
    "parse_json_bytes",
    "parse_json_text",
    "read_json_file",
    "serialize_json_bytes",
]
