"""Compare resolution, hash-enforced installation and live package inventory.

All JSON is untrusted boundary input. Missing/extra packages, altered artifacts,
wrong target, duplicate package names and non-isolated target prefixes fail.
This verifies a collected installation, NOT vulnerabilities, ABI or application IO.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from packaging.utils import canonicalize_name
from tools.staging_integrity import StagingError, seal_file, trusted_directory
from tools.dependency_lock import (
    LockError, MAX_PACKAGES, PIP_VERSION, LockedPackage, _object, _package, _text,
    read_report, verify_lock, windows_environment,
)


def package_map(report: dict[str, object]) -> dict[str, LockedPackage]:
    """Parse a report without treating all explicitly locked entries as root pins."""
    if report.get("version") != "1" or report.get("pip_version") != PIP_VERSION:
        raise LockError("installation report schema/pip version differs")
    env = windows_environment(report.get("environment"))
    raw = report.get("install")
    if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_PACKAGES:
        raise LockError("invalid installation package list")
    packages: dict[str, LockedPackage] = {}
    for item in raw:
        package = _package(item, env)
        if package.name in packages:
            raise LockError("duplicate installed package")
        packages[package.name] = package
    return packages


def installed_inventory(raw: dict[str, object], target: str) -> dict[str, str]:
    """Require an isolated Windows x64 CPython and a complete unique live list."""
    if raw.get("schema") != "wavehelm-live-inventory-v1":
        raise LockError("invalid live inventory schema")
    if (raw.get("platform") != "win32" or raw.get("implementation") != "CPython"
            or raw.get("bits") != 64 or type(raw.get("bits")) is not int
            or raw.get("version") != target or raw.get("isolated") is not True):
        raise LockError("live inventory target/isolation differs")
    prefix = _text(raw.get("prefix"), "prefix")
    base = _text(raw.get("base_prefix"), "base prefix")
    if prefix.casefold() == base.casefold() or raw.get("system_site_packages") is not False:
        raise LockError("live target is not a private virtual environment")
    rows = raw.get("packages")
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_PACKAGES:
        raise LockError("invalid live package inventory")
    result: dict[str, str] = {}
    for item in rows:
        row = _object(item, "live package")
        name = canonicalize_name(_text(row.get("name"), "live name", 256))
        version = _text(row.get("version"), "live version", 256)
        if name in result:
            raise LockError("duplicate live package")
        result[name] = version
    return result


def verify_installation(requirements: Path, bundle: Path, installation: Path,
                        live: Path, target: str) -> dict[str, object]:
    """Accept only equal package sets/versions and the selected artifact hashes."""
    trusted_directory(bundle)
    paths = (bundle / "requirements.lock", bundle / "pip-report.json", installation, live)
    seals = tuple(seal_file(path, str(index)) for index, path in enumerate(paths))
    digest = verify_lock(bundle / "requirements.lock", bundle / "pip-report.json", requirements)
    resolved = read_report(bundle / "pip-report.json")
    installed = read_report(installation)
    old_env = windows_environment(resolved.get("environment"))
    new_env = windows_environment(installed.get("environment"))
    if old_env != new_env or old_env["python_version"] != target:
        raise LockError("resolution and installation environment differ")
    expected, actual = package_map(resolved), package_map(installed)
    if set(expected) != set(actual):
        raise LockError("installed package set differs from resolution")
    for name, package in expected.items():
        observed = actual[name]
        if (package.version, package.filename, package.sha256) != (
                observed.version, observed.filename, observed.sha256):
            raise LockError(f"installed artifact differs: {name}")
    observed_versions = installed_inventory(read_report(live), target)
    if observed_versions != {name: p.version for name, p in expected.items()}:
        raise LockError("live packages differ from the selected installed graph")
    if tuple(seal_file(path, str(index)) for index, path in enumerate(paths)) != seals:
        raise LockError("installation comparison inputs changed while being verified")
    return {"schema": "wavehelm-installed-lock-v1", "status": "INSTALLED_GRAPH_MATCH_NOT_AUDITED",
            "target": target, "packages": len(expected), "lock_sha256": digest,
            "installation_sha256": seals[2].sha256,
            "live_inventory_sha256": seals[3].sha256,
            "vulnerability_status": "NOT_RUN", "application_status": "NOT_RUN"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("requirements", "bundle", "installation", "live"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--target", choices=("3.11", "3.12", "3.13"), required=True)
    args = parser.parse_args()
    try:
        result = verify_installation(args.requirements, args.bundle, args.installation,
                                     args.live, args.target)
    except (LockError, StagingError, OSError, UnicodeError) as exc:
        print(json.dumps({"status": "FAIL", "error": f"{type(exc).__name__}: {exc}"}))
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
