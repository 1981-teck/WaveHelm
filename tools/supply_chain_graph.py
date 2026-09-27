"""Validate installed package graphs and vulnerability evidence for WaveHelm."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from tools.supply_chain_contract import (
    TOOL_VERSIONS_SCHEMA,
    Policy,
    SupplyChainError,
    active_exception,
    canonicalize_name,
    read_exit_code,
    read_json,
)

MAX_FINDINGS = 2_000
MAX_VULNERABILITIES_PER_PACKAGE = 256


def _append(findings: list[str], value: str) -> None:
    if len(findings) < MAX_FINDINGS:
        findings.append(value)
    elif len(findings) == MAX_FINDINGS:
        findings.append("finding_limit_exceeded")


def _read_exit(path: Path, findings: list[str]) -> int | None:
    try:
        return read_exit_code(path)
    except SupplyChainError as exc:
        _append(findings, f"invalid_exit_code:{exc}")
        return None


def validate_execution_exit(path: Path, label: str, findings: list[str]) -> None:
    exit_code = _read_exit(path, findings)
    if exit_code not in {0, None}:
        _append(findings, f"{label}:exit:{exit_code}")


def _validate_download(
    item: dict[str, object], name: str, policy: Policy, findings: list[str]
) -> None:
    download = item.get("download_info")
    if type(download) is not dict or type(download.get("archive_info")) is not dict:
        _append(findings, f"install_report:download:{name}")
        return
    url = download.get("url")
    if policy.require_https_downloads and (
        type(url) is not str or urlparse(url).scheme.lower() != "https"
    ):
        _append(findings, f"install_report:non_https:{name}")
    hashes = download["archive_info"].get("hashes")
    digest = hashes.get("sha256") if type(hashes) is dict else None
    if type(digest) is not str or re.fullmatch(r"[0-9a-fA-F]{64}", digest) is None:
        _append(findings, f"install_report:missing_sha256:{name}")


def _install_items(path: Path, policy: Policy, findings: list[str]) -> list[dict[str, object]]:
    raw = read_json(path, policy.max_file_bytes)
    if type(raw) is not dict or type(raw.get("install")) is not list:
        _append(findings, "install_report:schema")
        return []
    items = raw["install"]
    if len(items) > policy.max_packages:
        _append(findings, "install_report:package_limit")
        return []
    result: list[dict[str, object]] = []
    for item in items:
        if type(item) is not dict:
            _append(findings, "install_report:item")
        else:
            result.append(item)
    return result


def _package_identity(
    item: dict[str, object], label: str, findings: list[str]
) -> tuple[str, str] | None:
    metadata = item.get("metadata")
    if type(metadata) is not dict:
        _append(findings, f"{label}:metadata")
        return None
    try:
        name = canonicalize_name(metadata.get("name"))
    except SupplyChainError:
        _append(findings, f"{label}:name")
        return None
    version = metadata.get("version")
    if type(version) is not str or not version or len(version) > 128:
        _append(findings, f"{label}:version:{name}")
        return None
    return name, version


def validate_tool_install_report(path: Path, policy: Policy, findings: list[str]) -> None:
    requested: dict[str, str] = {}
    for item in _install_items(path, policy, findings):
        identity = _package_identity(item, "tool_install_report", findings)
        if identity is None:
            continue
        name, version = identity
        _validate_download(item, name, policy, findings)
        if item.get("requested") is True:
            if name in requested:
                _append(findings, f"tool_install_report:duplicate_requested:{name}")
            requested[name] = version
    expected = {
        "pip-audit": policy.pip_audit_version,
        "cyclonedx-bom": policy.cyclonedx_bom_version,
    }
    if requested != expected:
        _append(findings, "tool_install_report:requested_mismatch")


def validate_tool_versions(path: Path, policy: Policy, findings: list[str]) -> None:
    raw = read_json(path, policy.max_file_bytes)
    if type(raw) is not dict or raw.get("schema") != TOOL_VERSIONS_SCHEMA:
        _append(findings, "tool_versions:schema")
        return
    tools = raw.get("tools")
    if type(tools) is not dict:
        _append(findings, "tool_versions:tools")
        return
    expected = {
        "pip": policy.pip_version,
        "pip-audit": policy.pip_audit_version,
        "cyclonedx-bom": policy.cyclonedx_bom_version,
    }
    if set(tools) != set(expected):
        _append(findings, "tool_versions:unexpected_keys")
    for name, version in expected.items():
        if tools.get(name) != version:
            _append(findings, f"tool_versions:mismatch:{name}:{tools.get(name)}")


def _validate_requested_packages(
    requested: set[str],
    installed: dict[str, str],
    requirements: dict[str, str],
    findings: list[str],
) -> None:
    direct = set(requirements)
    locked_graph = set(installed)
    if requested == direct or requested == locked_graph:
        return
    for name in sorted(direct - requested):
        _append(findings, f"install_report:missing_requested:{name}")
    for name in sorted(requested - direct):
        _append(findings, f"install_report:unexpected_requested:{name}")


def validate_install_report(
    path: Path,
    freeze: dict[str, str],
    requirements: dict[str, str],
    policy: Policy,
    findings: list[str],
) -> None:
    installed: dict[str, str] = {}
    requested: set[str] = set()
    for item in _install_items(path, policy, findings):
        identity = _package_identity(item, "install_report", findings)
        if identity is None:
            continue
        name, version = identity
        if name in installed:
            _append(findings, f"install_report:duplicate:{name}")
        installed[name] = version
        if item.get("requested") is True:
            requested.add(name)
        _validate_download(item, name, policy, findings)
    for name, version in freeze.items():
        if installed.get(name) != version:
            _append(findings, f"install_report:freeze_mismatch:{name}")
    for name in sorted(set(installed) - set(freeze)):
        _append(findings, f"install_report:unexpected:{name}")
    for name, version in requirements.items():
        if installed.get(name) != version:
            _append(findings, f"install_report:direct_requirement:{name}")
    _validate_requested_packages(requested, installed, requirements, findings)


def _audit_dependencies(raw: object, policy: Policy, findings: list[str]) -> list[dict[str, object]]:
    dependencies = raw.get("dependencies") if type(raw) is dict else raw
    if type(dependencies) is not list:
        _append(findings, "pip_audit:schema")
        return []
    if len(dependencies) > policy.max_packages:
        _append(findings, "pip_audit:package_limit")
        return []
    result: list[dict[str, object]] = []
    for item in dependencies:
        if type(item) is not dict:
            _append(findings, "pip_audit:dependency_shape")
        else:
            result.append(item)
    return result


def validate_audit(
    path: Path,
    exit_path: Path,
    freeze: dict[str, str],
    policy: Policy,
    findings: list[str],
) -> None:
    dependencies = _audit_dependencies(read_json(path, policy.max_file_bytes), policy, findings)
    observed: dict[str, str] = {}
    reported = 0
    blocking = 0
    now = datetime.now(timezone.utc)
    for item in dependencies:
        try:
            name = canonicalize_name(item.get("name"))
        except SupplyChainError:
            _append(findings, "pip_audit:name")
            continue
        version = item.get("version")
        vulnerabilities = item.get("vulns")
        if type(version) is not str or type(vulnerabilities) is not list:
            _append(findings, f"pip_audit:item:{name}")
            continue
        if name in observed:
            _append(findings, f"pip_audit:duplicate:{name}")
        observed[name] = version
        if len(vulnerabilities) > MAX_VULNERABILITIES_PER_PACKAGE:
            _append(findings, f"pip_audit:vulnerability_limit:{name}")
            continue
        for vulnerability in vulnerabilities:
            identifier = vulnerability.get("id") if type(vulnerability) is dict else None
            if type(identifier) is not str or not identifier or len(identifier) > 256:
                _append(findings, f"pip_audit:vulnerability_shape:{name}")
                continue
            reported += 1
            if active_exception(policy, name, version, identifier, now) is None:
                blocking += 1
                _append(findings, f"pip_audit:vulnerability:{name}:{identifier}")
    for name, version in freeze.items():
        if observed.get(name) != version:
            _append(findings, f"pip_audit:freeze_mismatch:{name}")
    for name in sorted(set(observed) - set(freeze)):
        _append(findings, f"pip_audit:unexpected:{name}")
    exit_code = _read_exit(exit_path, findings)
    expected_exit = 1 if reported else 0
    if exit_code is not None and exit_code != expected_exit:
        _append(findings, f"pip_audit:exit_mismatch:{exit_code}:{expected_exit}")
    if blocking and policy.fail_on_vulnerability:
        _append(findings, f"pip_audit:blocking_vulnerabilities:{blocking}")


def validate_pip_check(evidence: Path, prefix: str, findings: list[str]) -> None:
    exit_code = _read_exit(evidence / f"{prefix}-pip-check-exit-code.txt", findings)
    try:
        output = (evidence / f"{prefix}-pip-check.txt").read_text(encoding="utf-8-sig").strip()
    except (OSError, UnicodeError) as exc:
        _append(findings, f"{prefix}_pip_check:read:{exc}")
        return
    if exit_code != 0:
        _append(findings, f"{prefix}_pip_check:exit:{exit_code}")
    if output != "No broken requirements found.":
        _append(findings, f"{prefix}_pip_check:output")
