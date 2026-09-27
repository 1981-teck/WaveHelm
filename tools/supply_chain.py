"""Generate and verify WaveHelm supply-chain evidence."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import platform
import sys
import uuid
from pathlib import Path
from typing import Sequence
from urllib.parse import quote

# These command-line tools must not contaminate the source tree with bytecode.
sys.dont_write_bytecode = True

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.supply_chain_contract import (
    DIRECT_SBOM_SCHEMA,
    LICENSE_SCHEMA,
    TOOL_VERSIONS_SCHEMA,
    Policy,
    SupplyChainError,
    canonicalize_name,
    load_policy,
    parse_requirements,
    sha256_file,
    source_file_identity,
    validate_project_identity,
    write_json_atomic,
)
from tools.supply_chain_validation import create_manifest, validate_evidence, verify_manifest

LICENSE_PREFIXES = ("license", "licence", "copying", "notice", "authors")

def _project_component(policy: Policy) -> dict[str, object]:
    ref = f"pkg:pypi/{policy.project_name}@{quote(policy.project_version)}"
    return {
        "type": "application",
        "bom-ref": ref,
        "name": policy.project_name,
        "version": policy.project_version,
        "purl": ref,
    }


def generate_direct_sbom(root: Path, policy: Policy) -> dict[str, object]:
    validate_project_identity(root, policy)
    requirements = parse_requirements(root / "requirements.txt", policy.max_requirements)
    project = _project_component(policy)
    components: list[dict[str, object]] = []
    dependency_refs: list[str] = []
    identity_material: list[str] = [policy.project_name, policy.project_version]
    for item in sorted(requirements, key=lambda value: value.canonical_name):
        purl = f"pkg:pypi/{item.canonical_name}@{quote(item.version)}"
        component: dict[str, object] = {
            "type": "library",
            "bom-ref": purl,
            "name": item.name,
            "version": item.version,
            "purl": purl,
            "scope": "required",
        }
        if item.marker is not None:
            component["properties"] = [{"name": "wavehelm:pep508-marker", "value": item.marker}]
        components.append(component)
        dependency_refs.append(purl)
        identity_material.append(f"{item.canonical_name}=={item.version};{item.marker or ''}")
    serial = uuid.uuid5(uuid.NAMESPACE_URL, "\n".join(identity_material))
    return {
        "$schema": "https://cyclonedx.org/schema/bom-1.6.schema.json",
        "bomFormat": "CycloneDX",
        "specVersion": policy.sbom_spec_version,
        "serialNumber": f"urn:uuid:{serial}",
        "version": 1,
        "metadata": {
            "component": project,
            "properties": [
                {"name": "wavehelm:evidence-scope", "value": "declared-direct-dependencies"},
                {"name": "wavehelm:source-schema", "value": DIRECT_SBOM_SCHEMA},
            ],
        },
        "components": components,
        "dependencies": [
            {"ref": project["bom-ref"], "dependsOn": dependency_refs},
            *({"ref": ref, "dependsOn": []} for ref in dependency_refs),
        ],
    }


def _distribution_license_files(
    distribution: importlib.metadata.Distribution,
    site_packages: Path,
    policy: Policy,
) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    files = distribution.files or ()
    for package_file in files:
        lowered = package_file.name.lower()
        if not lowered.startswith(LICENSE_PREFIXES):
            continue
        located = Path(distribution.locate_file(package_file))
        try:
            if located.is_symlink():
                raise SupplyChainError(f"license payload is a symlink: {located}")
            resolved = located.resolve(strict=True)
            resolved.relative_to(site_packages)
        except (OSError, ValueError) as exc:
            raise SupplyChainError(f"license path escapes site-packages: {located}") from exc
        if resolved.is_symlink() or not resolved.is_file():
            raise SupplyChainError(f"license payload is not a regular file: {resolved}")
        digest, size = sha256_file(resolved, policy.max_license_file_bytes)
        result.append(
            {
                "path": resolved.relative_to(site_packages).as_posix(),
                "sha256": digest,
                "size": size,
            }
        )
        if len(result) > policy.max_license_files_per_package:
            raise SupplyChainError("package license file count exceeds policy")
    return sorted(result, key=lambda item: str(item["path"]))


def generate_license_inventory(
    root: Path, site_packages: Path, policy: Policy
) -> dict[str, object]:
    validate_project_identity(root, policy)
    if site_packages.is_symlink():
        raise SupplyChainError("site-packages must not be a symlink")
    resolved_site = site_packages.resolve(strict=True)
    if not resolved_site.is_dir():
        raise SupplyChainError("site-packages must be a real directory")
    packages: list[dict[str, object]] = []
    seen: set[str] = set()
    distributions = sorted(
        importlib.metadata.distributions(path=[str(resolved_site)]),
        key=lambda item: canonicalize_name(item.metadata["Name"]),
    )
    for distribution in distributions:
        name = distribution.metadata["Name"]
        version = distribution.version
        canonical = canonicalize_name(name)
        if canonical in seen:
            raise SupplyChainError(f"duplicate installed distribution: {canonical}")
        seen.add(canonical)
        classifiers = sorted(
            item for item in distribution.metadata.get_all("Classifier", []) if item.startswith("License ::")
        )
        packages.append(
            {
                "name": name,
                "canonical_name": canonical,
                "version": version,
                "license_expression": distribution.metadata.get("License-Expression") or "",
                "license_metadata": distribution.metadata.get("License") or "",
                "license_classifiers": classifiers,
                "license_files": _distribution_license_files(distribution, resolved_site, policy),
            }
        )
        if len(packages) > policy.max_packages:
            raise SupplyChainError("installed package count exceeds policy")
    source_files: list[dict[str, object]] = []
    for relative in policy.required_source_license_files:
        digest, size = source_file_identity(root, relative, policy.max_license_file_bytes)
        source_files.append({"path": relative, "sha256": digest, "size": size})
    return {
        "schema": LICENSE_SCHEMA,
        "project": {"name": policy.project_name, "version": policy.project_version},
        "site_packages": str(resolved_site),
        "packages": packages,
        "source_license_files": source_files,
    }


def generate_tool_versions(policy: Policy) -> dict[str, object]:
    names = {
        "pip": policy.pip_version,
        "pip-audit": policy.pip_audit_version,
        "cyclonedx-bom": policy.cyclonedx_bom_version,
    }
    installed: dict[str, str] = {}
    for name, expected in names.items():
        try:
            installed[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            installed[name] = "NOT_INSTALLED"
        if installed[name] not in {expected, "NOT_INSTALLED"}:
            raise SupplyChainError(f"{name} version mismatch: {installed[name]} != {expected}")
    return {
        "schema": TOOL_VERSIONS_SCHEMA,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "tools": installed,
    }


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("direct-sbom", "licenses", "tool-versions", "validate", "verify"))
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--policy", type=Path, default=Path("tools/supply_chain_policy.json"))
    parser.add_argument("--evidence-dir", type=Path)
    parser.add_argument("--site-packages", type=Path)
    parser.add_argument("--output", type=Path)
    return parser.parse_args(argv)


def _resolve(root: Path, path: Path | None) -> Path | None:
    if path is None or path.is_absolute():
        return path
    return root / path


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    root = args.root.resolve()
    policy_path = _resolve(root, args.policy)
    try:
        policy = load_policy(policy_path if policy_path is not None else Path())
        output = _resolve(root, args.output)
        evidence = _resolve(root, args.evidence_dir)
        site_packages = _resolve(root, args.site_packages)
        if args.command == "direct-sbom":
            payload = generate_direct_sbom(root, policy)
        elif args.command == "licenses":
            if site_packages is None:
                raise SupplyChainError("--site-packages is required")
            payload = generate_license_inventory(root, site_packages, policy)
        elif args.command == "tool-versions":
            payload = generate_tool_versions(policy)
        elif args.command == "validate":
            if evidence is None:
                raise SupplyChainError("--evidence-dir is required")
            evidence.mkdir(parents=True, exist_ok=True)
            payload = validate_evidence(root, evidence, policy)
            write_json_atomic(evidence / "supply-chain-status.json", payload)
            manifest = create_manifest(evidence, policy)
            write_json_atomic(evidence / "supply-chain-evidence-manifest.json", manifest)
            output = output or evidence / "supply-chain-status.json"
        else:
            if evidence is None:
                raise SupplyChainError("--evidence-dir is required")
            payload = verify_manifest(evidence, policy)
        if output is not None and not (args.command == "validate" and output.name == "supply-chain-status.json"):
            write_json_atomic(output, payload)
        elif output is None:
            sys.stdout.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        return 0 if payload.get("verdict", "PASS") == "PASS" else 1
    except SupplyChainError as exc:
        sys.stderr.write(f"supply-chain error: {exc}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
