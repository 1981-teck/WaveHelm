"""Typed contracts and bounded parsers for WaveHelm supply-chain evidence."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tomllib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence

POLICY_SCHEMA = "wavehelm-supply-chain-policy-v1"
STATUS_SCHEMA = "wavehelm-supply-chain-status-v1"
MANIFEST_SCHEMA = "wavehelm-supply-chain-evidence-manifest-v1"
DIRECT_SBOM_SCHEMA = "wavehelm-declared-direct-sbom-v1"
LICENSE_SCHEMA = "wavehelm-runtime-license-inventory-v1"
TOOL_VERSIONS_SCHEMA = "wavehelm-supply-chain-tool-versions-v1"
NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
VERSION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.!+_-]*$")
PIN_PATTERN = re.compile(
    r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)=="
    r"(?P<version>[A-Za-z0-9][A-Za-z0-9.!+_-]*)"
    r"(?:\s*;\s*(?P<marker>[^#]+?))?\s*$"
)
HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")
MAX_JSON_DEPTH = 24


class SupplyChainError(RuntimeError):
    """Raised when supply-chain evidence violates its explicit contract."""


@dataclass(frozen=True)
class RequirementPin:
    name: str
    canonical_name: str
    version: str
    marker: str | None


@dataclass(frozen=True)
class VulnerabilityException:
    package: str
    version: str
    vulnerability_id: str
    expires_utc: datetime
    reason: str


@dataclass(frozen=True)
class Policy:
    project_name: str
    project_version: str
    sbom_spec_version: str
    pip_version: str
    pip_audit_version: str
    cyclonedx_bom_version: str
    max_requirements: int
    max_packages: int
    max_file_bytes: int
    max_license_files_per_package: int
    max_license_file_bytes: int
    require_https_downloads: bool
    fail_on_vulnerability: bool
    required_source_license_files: tuple[str, ...]
    vulnerability_exceptions: tuple[VulnerabilityException, ...]


@dataclass(frozen=True)
class PackagePin:
    name: str
    canonical_name: str
    version: str


@dataclass(frozen=True)
class ValidationReport:
    findings: tuple[str, ...]
    details: Mapping[str, object]

    @property
    def passed(self) -> bool:
        return not self.findings


def canonicalize_name(name: str) -> str:
    if type(name) is not str or NAME_PATTERN.fullmatch(name) is None:
        raise SupplyChainError(f"invalid package name: {name!r}")
    return re.sub(r"[-_.]+", "-", name).lower()


def _exact_int(value: object, name: str, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise SupplyChainError(f"{name} must be an integer in {minimum}..{maximum}")
    return value


def _exact_bool(value: object, name: str) -> bool:
    if type(value) is not bool:
        raise SupplyChainError(f"{name} must be a boolean")
    return value


def _string(value: object, name: str, maximum: int = 1_024) -> str:
    if type(value) is not str or not value or len(value) > maximum:
        raise SupplyChainError(f"{name} must be a non-empty bounded string")
    return value


def _string_list(value: object, name: str, maximum: int = 1_024) -> tuple[str, ...]:
    if type(value) is not list or len(value) > maximum:
        raise SupplyChainError(f"{name} must be a bounded array")
    result = tuple(_string(item, f"{name}[]", 4_096) for item in value)
    if len(result) != len(set(result)):
        raise SupplyChainError(f"{name} contains duplicates")
    return result


def _safe_relative_list(value: object, name: str) -> tuple[str, ...]:
    result = _string_list(value, name, 256)
    for item in result:
        path = Path(item)
        if path.is_absolute() or "\\" in item or ".." in path.parts:
            raise SupplyChainError(f"{name} contains an unsafe path")
        if path.as_posix() != item or item.startswith("/"):
            raise SupplyChainError(f"{name} contains a non-canonical path")
    return result


def _parse_timestamp(value: object, name: str) -> datetime:
    text = _string(value, name, 64)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SupplyChainError(f"{name} is not an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise SupplyChainError(f"{name} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _parse_exceptions(value: object) -> tuple[VulnerabilityException, ...]:
    if type(value) is not list or len(value) > 128:
        raise SupplyChainError("vulnerability_exceptions must be a bounded array")
    result: list[VulnerabilityException] = []
    keys: set[tuple[str, str]] = set()
    for index, item in enumerate(value):
        if type(item) is not dict:
            raise SupplyChainError(f"vulnerability_exceptions[{index}] must be an object")
        expected = {"package", "version", "vulnerability_id", "expires_utc", "reason"}
        if set(item) != expected:
            raise SupplyChainError(f"vulnerability_exceptions[{index}] has invalid keys")
        package = canonicalize_name(_string(item["package"], "package"))
        version = _string(item["version"], "version", 128)
        if VERSION_PATTERN.fullmatch(version) is None:
            raise SupplyChainError("vulnerability exception version is invalid")
        vulnerability_id = _string(item["vulnerability_id"], "vulnerability_id", 256)
        expires = _parse_timestamp(item["expires_utc"], "expires_utc")
        reason = _string(item["reason"], "reason", 2_048)
        key = (package, version, vulnerability_id)
        if key in keys:
            raise SupplyChainError("duplicate vulnerability exception")
        keys.add(key)
        result.append(VulnerabilityException(package, version, vulnerability_id, expires, reason))
    return tuple(result)


def load_policy(path: Path) -> Policy:
    raw = read_json(path, 1_048_576)
    if type(raw) is not dict or raw.get("schema") != POLICY_SCHEMA:
        raise SupplyChainError("unsupported supply-chain policy schema")
    project = raw.get("project")
    tools = raw.get("tools")
    limits = raw.get("limits")
    if type(project) is not dict or type(tools) is not dict or type(limits) is not dict:
        raise SupplyChainError("policy project, tools and limits must be objects")
    project_name = canonicalize_name(_string(project.get("name"), "project.name"))
    project_version = _string(project.get("version"), "project.version", 128)
    if VERSION_PATTERN.fullmatch(project_version) is None:
        raise SupplyChainError("project.version is invalid")
    return Policy(
        project_name=project_name,
        project_version=project_version,
        sbom_spec_version=_string(raw.get("sbom_spec_version"), "sbom_spec_version", 16),
        pip_version=_string(tools.get("pip"), "tools.pip", 64),
        pip_audit_version=_string(tools.get("pip-audit"), "tools.pip-audit", 64),
        cyclonedx_bom_version=_string(tools.get("cyclonedx-bom"), "tools.cyclonedx-bom", 64),
        max_requirements=_exact_int(limits.get("max_requirements"), "max_requirements", 1, 1_024),
        max_packages=_exact_int(limits.get("max_packages"), "max_packages", 1, 10_000),
        max_file_bytes=_exact_int(limits.get("max_file_bytes"), "max_file_bytes", 1, 64 * 1024 * 1024),
        max_license_files_per_package=_exact_int(
            limits.get("max_license_files_per_package"),
            "max_license_files_per_package",
            1,
            256,
        ),
        max_license_file_bytes=_exact_int(
            limits.get("max_license_file_bytes"),
            "max_license_file_bytes",
            1,
            16 * 1024 * 1024,
        ),
        require_https_downloads=_exact_bool(
            raw.get("require_https_downloads"), "require_https_downloads"
        ),
        fail_on_vulnerability=_exact_bool(
            raw.get("fail_on_vulnerability"), "fail_on_vulnerability"
        ),
        required_source_license_files=_safe_relative_list(
            raw.get("required_source_license_files"), "required_source_license_files"
        ),
        vulnerability_exceptions=_parse_exceptions(raw.get("vulnerability_exceptions")),
    )


def _validate_json_shape(value: object, depth: int = 0) -> None:
    if depth > MAX_JSON_DEPTH:
        raise SupplyChainError("JSON evidence exceeds maximum depth")
    if value is None or type(value) in {bool, str}:
        return
    if type(value) is int:
        if not -(2**63) <= value <= 2**63 - 1:
            raise SupplyChainError("JSON evidence integer exceeds signed 64-bit range")
        return
    if type(value) is float:
        if not math.isfinite(value):
            raise SupplyChainError("JSON evidence contains a non-finite number")
        return
    if type(value) is list:
        for item in value:
            _validate_json_shape(item, depth + 1)
        return
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str:
                raise SupplyChainError("JSON evidence contains a non-string key")
            _validate_json_shape(item, depth + 1)
        return
    raise SupplyChainError("JSON evidence contains an unsupported value")


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise SupplyChainError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def read_json(path: Path, maximum_bytes: int) -> object:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise SupplyChainError(f"cannot read {path}: {exc}") from exc
    if len(data) > maximum_bytes:
        raise SupplyChainError(f"{path.name} exceeds the byte budget")
    try:
        raw = json.loads(
            data.decode("utf-8-sig"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise SupplyChainError(f"cannot parse {path}: {exc}") from exc
    _validate_json_shape(raw)
    return raw


def _reject_constant(value: str) -> object:
    raise ValueError(f"non-standard JSON constant: {value}")


def validate_project_identity(root: Path, policy: Policy) -> None:
    pyproject_path = root / "pyproject.toml"
    try:
        data = pyproject_path.read_bytes()
    except OSError as exc:
        raise SupplyChainError(f"cannot read pyproject.toml: {exc}") from exc
    if len(data) > 1_048_576:
        raise SupplyChainError("pyproject.toml exceeds the byte budget")
    try:
        pyproject = tomllib.loads(data.decode("utf-8"))
    except (UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise SupplyChainError(f"cannot parse pyproject.toml: {exc}") from exc
    project = pyproject.get("project")
    if type(project) is not dict:
        raise SupplyChainError("pyproject.toml has no project table")
    name = canonicalize_name(project.get("name"))
    version = project.get("version")
    if name != policy.project_name or version != policy.project_version:
        raise SupplyChainError("project identity differs from supply-chain policy")
    app_info = read_json(root / "src" / "config" / "app_info.json", 1_048_576)
    general = app_info.get("general") if type(app_info) is dict else None
    if type(general) is not dict or general.get("version") != policy.project_version:
        raise SupplyChainError("runtime version differs from supply-chain policy")


def parse_requirements(path: Path, maximum: int) -> tuple[RequirementPin, ...]:
    maximum = _exact_int(maximum, "requirements maximum", 1, 10_000)
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except (OSError, UnicodeError) as exc:
        raise SupplyChainError(f"cannot read requirements: {exc}") from exc
    result: list[RequirementPin] = []
    seen: set[str] = set()
    for number, raw_line in enumerate(lines, 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = PIN_PATTERN.fullmatch(line)
        if match is None:
            raise SupplyChainError(f"requirements line {number} is not an exact pin")
        name = match.group("name")
        canonical = canonicalize_name(name)
        version = match.group("version")
        marker = match.group("marker")
        marker = marker.strip() if marker is not None else None
        if canonical in seen:
            raise SupplyChainError(f"duplicate requirement: {canonical}")
        seen.add(canonical)
        result.append(RequirementPin(name, canonical, version, marker))
        if len(result) > maximum:
            raise SupplyChainError("requirements exceed the package budget")
    if not result:
        raise SupplyChainError("requirements are empty")
    return tuple(result)


def parse_freeze(path: Path, maximum: int) -> tuple[PackagePin, ...]:
    maximum = _exact_int(maximum, "freeze maximum", 1, 10_000)
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except (OSError, UnicodeError) as exc:
        raise SupplyChainError(f"cannot read runtime freeze: {exc}") from exc
    result: list[PackagePin] = []
    seen: set[str] = set()
    for number, raw_line in enumerate(lines, 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = PIN_PATTERN.fullmatch(line)
        if match is None or match.group("marker") is not None:
            raise SupplyChainError(f"freeze line {number} is not an exact installed pin")
        name = match.group("name")
        canonical = canonicalize_name(name)
        if canonical in seen:
            raise SupplyChainError(f"duplicate freeze package: {canonical}")
        seen.add(canonical)
        result.append(PackagePin(name, canonical, match.group("version")))
        if len(result) > maximum:
            raise SupplyChainError("freeze exceeds the package budget")
    if not result:
        raise SupplyChainError("runtime freeze is empty")
    return tuple(result)


def sha256_file(path: Path, maximum_bytes: int | None = None) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                size += len(chunk)
                if maximum_bytes is not None and size > maximum_bytes:
                    raise SupplyChainError(f"{path.name} exceeds the hash byte budget")
                digest.update(chunk)
    except OSError as exc:
        raise SupplyChainError(f"cannot hash {path}: {exc}") from exc
    return digest.hexdigest(), size


def read_exit_code(path: Path) -> int:
    try:
        text = path.read_text(encoding="utf-8-sig").strip()
        value = int(text)
    except (OSError, UnicodeError, ValueError) as exc:
        raise SupplyChainError(f"invalid exit code {path.name}: {exc}") from exc
    if not 0 <= value <= 255:
        raise SupplyChainError(f"invalid exit code {path.name}: {value}")
    return value


def source_file_identity(
    root: Path, relative: str, maximum_bytes: int
) -> tuple[str, int]:
    source_root = root.resolve(strict=True)
    path = root / relative
    if path.is_symlink():
        raise SupplyChainError(f"source license file is a symlink: {relative}")
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(source_root)
    except (OSError, ValueError) as exc:
        raise SupplyChainError(f"source license file escapes root: {relative}") from exc
    if not resolved.is_file():
        raise SupplyChainError(f"source license path is not a file: {relative}")
    return sha256_file(resolved, maximum_bytes)


def write_json_atomic(path: Path, payload: Mapping[str, object]) -> None:
    data = (json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".tmp-{os.getpid()}")
    try:
        with temporary.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except OSError as exc:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise SupplyChainError(f"cannot write {path}: {exc}") from exc


def active_exception(
    policy: Policy, package: str, version: str, vulnerability_id: str, now: datetime
) -> VulnerabilityException | None:
    canonical = canonicalize_name(package)
    for item in policy.vulnerability_exceptions:
        matches = (
            item.package == canonical
            and item.version == version
            and item.vulnerability_id == vulnerability_id
        )
        if matches:
            return item if item.expires_utc > now.astimezone(timezone.utc) else None
    return None
