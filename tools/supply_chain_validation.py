"""Validate WaveHelm installed-graph supply-chain evidence."""

from __future__ import annotations

from pathlib import Path

from tools.supply_chain_contract import (
    LICENSE_SCHEMA,
    STATUS_SCHEMA,
    Policy,
    SupplyChainError,
    canonicalize_name,
    parse_freeze,
    parse_requirements,
    read_exit_code,
    read_json,
    source_file_identity,
    validate_project_identity,
)
from tools.supply_chain_graph import (
    validate_audit,
    validate_execution_exit,
    validate_install_report,
    validate_pip_check,
    validate_tool_install_report,
    validate_tool_versions,
)
from tools.supply_chain_manifest import create_manifest, verify_manifest

EVIDENCE_FILES = (
    "pip-bootstrap-exit-code.txt",
    "audit-tool-install-report.json",
    "audit-tool-install-exit-code.txt",
    "tool-versions-exit-code.txt",
    "supply-chain-tool-versions.json",
    "runtime-install-report.json",
    "runtime-install-exit-code.txt",
    "runtime-freeze.txt",
    "runtime-freeze-exit-code.txt",
    "runtime-pip-check.txt",
    "runtime-pip-check-exit-code.txt",
    "pip-audit.json",
    "pip-audit-exit-code.txt",
    "sbom.cdx.json",
    "sbom-exit-code.txt",
    "runtime-license-inventory.json",
    "license-inventory-exit-code.txt",
)
MAX_FINDINGS = 2_000
MAX_LICENSE_FILES = 256


def _append(findings: list[str], finding: str) -> None:
    if len(findings) < MAX_FINDINGS:
        findings.append(finding)
    elif len(findings) == MAX_FINDINGS:
        findings.append("finding_limit_exceeded")


def _record_exit_code(path: Path, findings: list[str]) -> int | None:
    try:
        return read_exit_code(path)
    except SupplyChainError as exc:
        _append(findings, f"invalid_exit_code:{exc}")
        return None


def _sbom_components(
    raw: dict[str, object], policy: Policy, findings: list[str]
) -> tuple[dict[str, str], set[str]]:
    components = raw.get("components")
    if type(components) is not list or len(components) > policy.max_packages:
        _append(findings, "sbom:components")
        return {}, set()
    observed: dict[str, str] = {}
    refs: set[str] = set()
    for item in components:
        if type(item) is not dict:
            _append(findings, "sbom:component_shape")
            continue
        try:
            name = canonicalize_name(item.get("name"))
        except SupplyChainError:
            _append(findings, "sbom:component_name")
            continue
        version = item.get("version")
        reference = item.get("bom-ref")
        if type(version) is not str or not version or type(reference) is not str or not reference:
            _append(findings, f"sbom:component_fields:{name}")
            continue
        if name in observed or reference in refs:
            _append(findings, f"sbom:duplicate:{name}")
        observed[name] = version
        refs.add(reference)
    return observed, refs


def _validate_dependency_refs(
    raw: dict[str, object], allowed_refs: set[str], policy: Policy, findings: list[str]
) -> None:
    dependencies = raw.get("dependencies")
    if type(dependencies) is not list or len(dependencies) > policy.max_packages + 1:
        _append(findings, "sbom:dependencies")
        return
    observed_refs: set[str] = set()
    for dependency in dependencies:
        reference = dependency.get("ref") if type(dependency) is dict else None
        if type(reference) is not str or reference not in allowed_refs:
            _append(findings, "sbom:dependency_ref")
            continue
        if reference in observed_refs:
            _append(findings, f"sbom:duplicate_dependency:{reference}")
        observed_refs.add(reference)
        depends_on = dependency.get("dependsOn", [])
        if type(depends_on) is not list or len(depends_on) > policy.max_packages:
            _append(findings, f"sbom:depends_on:{reference}")
            continue
        if len(depends_on) != len(set(depends_on)) or any(item not in allowed_refs for item in depends_on):
            _append(findings, f"sbom:depends_on:{reference}")
    for reference in sorted(allowed_refs - observed_refs):
        _append(findings, f"sbom:missing_dependency:{reference}")


def _validate_sbom(
    path: Path, exit_path: Path, freeze: dict[str, str], policy: Policy, findings: list[str]
) -> None:
    exit_code = _record_exit_code(exit_path, findings)
    if exit_code not in {0, None}:
        _append(findings, f"sbom:tool_exit:{exit_code}")
    raw = read_json(path, policy.max_file_bytes)
    if type(raw) is not dict or raw.get("bomFormat") != "CycloneDX":
        _append(findings, "sbom:format")
        return
    if raw.get("specVersion") != policy.sbom_spec_version:
        _append(findings, f"sbom:spec_version:{raw.get('specVersion')}")
    metadata = raw.get("metadata")
    project = metadata.get("component") if type(metadata) is dict else None
    if type(project) is not dict:
        _append(findings, "sbom:project_component")
    else:
        try:
            project_name = canonicalize_name(project.get("name"))
        except SupplyChainError:
            project_name = ""
        if project_name != policy.project_name or project.get("version") != policy.project_version:
            _append(findings, "sbom:project_identity")
    observed, refs = _sbom_components(raw, policy, findings)
    for name, version in freeze.items():
        if observed.get(name) != version:
            _append(findings, f"sbom:freeze_mismatch:{name}")
    for name in sorted(set(observed) - set(freeze)):
        _append(findings, f"sbom:unexpected:{name}")
    allowed_refs = set(refs)
    if type(project) is dict and type(project.get("bom-ref")) is str:
        allowed_refs.add(project["bom-ref"])
    _validate_dependency_refs(raw, allowed_refs, policy, findings)


def _valid_license_files(value: object, package: str, findings: list[str]) -> bool:
    if type(value) is not list or len(value) > MAX_LICENSE_FILES:
        _append(findings, f"license_inventory:license_files:{package}")
        return False
    paths: set[str] = set()
    valid = False
    for item in value:
        if type(item) is not dict or set(item) != {"path", "sha256", "size"}:
            _append(findings, f"license_inventory:license_file_shape:{package}")
            continue
        path = item["path"]
        digest = item["sha256"]
        size = item["size"]
        if type(path) is not str or not path or path in paths:
            _append(findings, f"license_inventory:license_file_path:{package}")
            continue
        paths.add(path)
        if type(digest) is not str or len(digest) != 64 or type(size) is not int or size < 0:
            _append(findings, f"license_inventory:license_file_identity:{package}")
            continue
        valid = True
    return valid


def _license_packages(
    packages: list[object], freeze: dict[str, str], findings: list[str]
) -> None:
    observed: dict[str, str] = {}
    for item in packages:
        if type(item) is not dict:
            _append(findings, "license_inventory:package")
            continue
        try:
            name = canonicalize_name(item.get("name"))
        except SupplyChainError:
            _append(findings, "license_inventory:name")
            continue
        version = item.get("version")
        if type(version) is not str or not version:
            _append(findings, f"license_inventory:version:{name}")
            continue
        if name in observed:
            _append(findings, f"license_inventory:duplicate:{name}")
        observed[name] = version
        if item.get("canonical_name") != name:
            _append(findings, f"license_inventory:canonical_name:{name}")
        metadata = any(
            bool(item.get(key))
            for key in ("license_expression", "license_metadata", "license_classifiers")
        )
        file_evidence = _valid_license_files(item.get("license_files"), name, findings)
        if not metadata and not file_evidence:
            _append(findings, f"license_inventory:missing_license_evidence:{name}")
    for name, version in freeze.items():
        if observed.get(name) != version:
            _append(findings, f"license_inventory:freeze_mismatch:{name}")
    for name in sorted(set(observed) - set(freeze)):
        _append(findings, f"license_inventory:unexpected:{name}")


def _source_license_files(
    root: Path, source_files: list[object], policy: Policy, findings: list[str]
) -> None:
    source_map: dict[str, dict[str, object]] = {}
    for item in source_files:
        relative = item.get("path") if type(item) is dict else None
        if type(relative) is not str or relative in source_map:
            _append(findings, "license_inventory:source_shape_or_duplicate")
        else:
            source_map[relative] = item
    for required in policy.required_source_license_files:
        item = source_map.get(required)
        if item is None:
            _append(findings, f"license_inventory:source_missing:{required}")
            continue
        try:
            digest, size = source_file_identity(root, required, policy.max_license_file_bytes)
        except SupplyChainError as exc:
            _append(findings, f"license_inventory:source_error:{required}:{exc}")
            continue
        if item.get("sha256") != digest or item.get("size") != size:
            _append(findings, f"license_inventory:source_mismatch:{required}")
    for relative in sorted(set(source_map) - set(policy.required_source_license_files)):
        _append(findings, f"license_inventory:source_unexpected:{relative}")


def _validate_license_inventory(
    root: Path,
    path: Path,
    exit_path: Path,
    freeze: dict[str, str],
    policy: Policy,
    findings: list[str],
) -> None:
    exit_code = _record_exit_code(exit_path, findings)
    if exit_code not in {0, None}:
        _append(findings, f"license_inventory:tool_exit:{exit_code}")
    raw = read_json(path, policy.max_file_bytes)
    if type(raw) is not dict or raw.get("schema") != LICENSE_SCHEMA:
        _append(findings, "license_inventory:schema")
        return
    if raw.get("project") != {"name": policy.project_name, "version": policy.project_version}:
        _append(findings, "license_inventory:project_identity")
    packages = raw.get("packages")
    source_files = raw.get("source_license_files")
    valid_shape = (
        type(packages) is list
        and len(packages) <= policy.max_packages
        and type(source_files) is list
        and len(source_files) <= MAX_LICENSE_FILES
    )
    if not valid_shape:
        _append(findings, "license_inventory:shape")
        return
    _license_packages(packages, freeze, findings)
    _source_license_files(root, source_files, policy, findings)


def validate_evidence(root: Path, evidence: Path, policy: Policy) -> dict[str, object]:
    findings: list[str] = []
    try:
        validate_project_identity(root, policy)
    except SupplyChainError as exc:
        _append(findings, f"project_identity:{exc}")
    for relative in EVIDENCE_FILES:
        if not (evidence / relative).is_file():
            _append(findings, f"evidence:missing:{relative}")
    try:
        requirements = parse_requirements(root / "requirements.txt", policy.max_requirements)
    except SupplyChainError as exc:
        _append(findings, f"requirements:{exc}")
        requirements = ()
    try:
        freeze = parse_freeze(evidence / "runtime-freeze.txt", policy.max_packages)
    except SupplyChainError as exc:
        _append(findings, f"freeze:{exc}")
        freeze = ()
    requirement_map = {item.canonical_name: item.version for item in requirements}
    freeze_map = {item.canonical_name: item.version for item in freeze}
    for name, version in requirement_map.items():
        if freeze_map.get(name) != version:
            _append(findings, f"freeze:direct_requirement:{name}")
    validators = (
        lambda: validate_execution_exit(evidence / "pip-bootstrap-exit-code.txt", "pip_bootstrap", findings),
        lambda: validate_execution_exit(
            evidence / "audit-tool-install-exit-code.txt", "tool_install", findings
        ),
        lambda: validate_execution_exit(
            evidence / "tool-versions-exit-code.txt", "tool_versions", findings
        ),
        lambda: validate_execution_exit(
            evidence / "runtime-install-exit-code.txt", "runtime_install", findings
        ),
        lambda: validate_execution_exit(
            evidence / "runtime-freeze-exit-code.txt", "runtime_freeze", findings
        ),
        lambda: validate_tool_install_report(
            evidence / "audit-tool-install-report.json", policy, findings
        ),
        lambda: validate_tool_versions(evidence / "supply-chain-tool-versions.json", policy, findings),
        lambda: validate_install_report(
            evidence / "runtime-install-report.json", freeze_map, requirement_map, policy, findings
        ),
        lambda: validate_pip_check(evidence, "runtime", findings),
        lambda: validate_audit(
            evidence / "pip-audit.json", evidence / "pip-audit-exit-code.txt", freeze_map, policy, findings
        ),
        lambda: _validate_sbom(
            evidence / "sbom.cdx.json", evidence / "sbom-exit-code.txt", freeze_map, policy, findings
        ),
        lambda: _validate_license_inventory(
            root,
            evidence / "runtime-license-inventory.json",
            evidence / "license-inventory-exit-code.txt",
            freeze_map,
            policy,
            findings,
        ),
    )
    for validator in validators:
        try:
            validator()
        except SupplyChainError as exc:
            _append(findings, f"validation_error:{exc}")
    return {
        "schema": STATUS_SCHEMA,
        "verdict": "PASS" if not findings else "FAIL",
        "project": {"name": policy.project_name, "version": policy.project_version},
        "platform_requirement": "Windows installed runtime dependency graph",
        "requirements_count": len(requirements),
        "installed_package_count": len(freeze),
        "vulnerability_exception_count": len(policy.vulnerability_exceptions),
        "findings": findings,
    }
