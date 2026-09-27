"""Validate a complete Windows pip graph and render exact artifact hash locks.

Cold tooling boundary. Rejects incomplete graphs, foreign wheel tags, duplicate
packages, unsupported markers/extras, missing hashes and unrequested packages.
Input is a pip report, not proof that installation or vulnerability scanning ran.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit

from packaging.markers import default_environment
from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.tags import compatible_tags, cpython_tags
from packaging.utils import InvalidWheelFilename, canonicalize_name, parse_wheel_filename
from packaging.version import InvalidVersion, Version

MAX_BYTES = 32 * 1024 * 1024
MAX_PACKAGES = 2048
PIP_VERSION = "26.2.1"


class LockError(ValueError):
    """The declared graph cannot be promoted to a reproducible hash lock."""


@dataclass(frozen=True)
class LockedPackage:
    name: str
    version: str
    sha256: str
    filename: str
    requirements: tuple[Requirement, ...]
    requires_python: str
    requested: bool


def _object(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(k, str) for k in value):
        raise LockError(f"{label}: expected object")
    return value


def _text(value: object, label: str, limit: int = 4096) -> str:
    if not isinstance(value, str) or not value or len(value) > limit:
        raise LockError(f"{label}: expected bounded string")
    return value


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise LockError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def read_report(path: Path) -> dict[str, object]:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_BYTES:
        raise LockError("report missing, linked or oversized")
    try:
        with path.open("rb") as stream:
            data = stream.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise LockError("report grew beyond budget")
        return _object(json.loads(data, object_pairs_hook=_unique_object), "report")
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise LockError(f"cannot parse report: {exc}") from exc


def windows_environment(raw: object) -> dict[str, str]:
    source = _object(raw, "environment")
    env = {key: _text(value, f"environment.{key}") for key, value in source.items()}
    if not set(default_environment()).issubset(env):
        raise LockError("report omits required environment marker fields")
    expected = {"platform_system": "Windows", "os_name": "nt", "sys_platform": "win32",
                "implementation_name": "cpython", "platform_python_implementation": "CPython"}
    if any(env.get(k) != v for k, v in expected.items()):
        raise LockError("report must describe native Windows CPython")
    if env.get("platform_machine", "").lower() not in {"amd64", "x86_64"}:
        raise LockError("report must describe Windows x64")
    if env.get("python_version") not in {"3.11", "3.12", "3.13"}:
        raise LockError("unsupported Python version")
    if not env.get("python_full_version", "").startswith(env["python_version"] + "."):
        raise LockError("inconsistent Python version fields")
    env["extra"] = ""
    return env


def parse_requirement(text: str) -> Requirement:
    try:
        item = Requirement(text)
    except InvalidRequirement as exc:
        raise LockError(f"invalid requirement: {text}") from exc
    if item.url is not None or len(item.extras) > 64:
        raise LockError("URL requirements or excessive extras are not supported")
    return item


def root_requirements(path: Path, env: dict[str, str]) -> tuple[Requirement, ...]:
    """Load a bounded, contained include tree; reject cycles and unpinned roots."""
    base = path.resolve().parent
    pending = [path]
    seen: set[Path] = set()
    roots: dict[str, Requirement] = {}
    while pending:
        current = pending.pop()
        if current.is_symlink() or not current.is_file():
            raise LockError("missing or linked requirements file")
        current = current.resolve()
        if current in seen or not current.is_relative_to(base) or len(seen) >= 16:
            raise LockError("requirements include cycle, escape or budget exceeded")
        seen.add(current)
        if current.stat().st_size > 128 * 1024:
            raise LockError("oversized requirements file")
        for raw in current.read_text(encoding="utf-8").splitlines():
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            if line.startswith("-r "):
                pending.append(current.parent / line[3:].strip())
                continue
            item = parse_requirement(line)
            specs = list(item.specifier)
            if len(specs) != 1 or specs[0].operator != "==" or "*" in specs[0].version:
                raise LockError("all root requirements must be exact pins")
            if item.marker is not None and not item.marker.evaluate(env):
                continue
            name = canonicalize_name(item.name)
            if name in roots or len(roots) >= MAX_PACKAGES:
                raise LockError("duplicate roots or root budget exceeded")
            roots[name] = item
    if not roots:
        raise LockError("empty requirements")
    return tuple(roots[name] for name in sorted(roots))


def _wheel(download: dict[str, object], name: str, version: str, env: dict[str, str]) -> tuple[str, str]:
    address = urlsplit(_text(download.get("url"), "download URL"))
    if (address.scheme != "https" or address.hostname != "files.pythonhosted.org"
            or address.username or address.password or address.query or address.fragment):
        raise LockError("only canonical HTTPS PyPI wheel URLs are permitted")
    filename = unquote(address.path.rsplit("/", 1)[-1])
    try:
        wheel_name, wheel_version, _, tags = parse_wheel_filename(filename)
        minor = tuple(int(n) for n in env["python_version"].split("."))
        allowed = set(cpython_tags(minor, platforms=["win_amd64"]))
        allowed.update(compatible_tags(minor, interpreter="cp" + "".join(map(str, minor)),
                                       platforms=["win_amd64"]))
        if wheel_name != name or wheel_version != Version(version) or not tags & allowed:
            raise LockError("wheel identity or target tags do not match")
    except (InvalidWheelFilename, InvalidVersion) as exc:
        raise LockError(f"invalid wheel identity: {exc}") from exc
    archive = _object(download.get("archive_info"), "archive_info")
    hashes = _object(archive.get("hashes"), "hashes")
    digest = _text(hashes.get("sha256"), "sha256", 64)
    if re.fullmatch(r"[a-f0-9]{64}", digest) is None:
        raise LockError("invalid artifact SHA-256")
    return filename, digest


def _package(raw: object, env: dict[str, str]) -> LockedPackage:
    item = _object(raw, "package")
    if item.get("is_direct") is not False or item.get("is_yanked") is not False:
        raise LockError("direct URL, yanked or unspecified package provenance")
    if type(item.get("requested")) is not bool:
        raise LockError("requested must be a boolean")
    metadata = _object(item.get("metadata"), "metadata")
    name = canonicalize_name(_text(metadata.get("name"), "name", 256))
    version = _text(metadata.get("version"), "version", 256)
    download = _object(item.get("download_info"), "download_info")
    filename, digest = _wheel(download, name, version, env)
    raw_requires = metadata.get("requires_dist", [])
    if not isinstance(raw_requires, list) or len(raw_requires) > 256:
        raise LockError("invalid requires_dist")
    requirements = tuple(parse_requirement(_text(v, "dependency")) for v in raw_requires)
    python_spec = metadata.get("requires_python", "")
    if not isinstance(python_spec, str):
        raise LockError("invalid requires_python")
    try:
        if not SpecifierSet(python_spec).contains(env["python_full_version"], prereleases=True):
            raise LockError("package does not support target Python")
    except InvalidSpecifier as exc:
        raise LockError(str(exc)) from exc
    return LockedPackage(name, version, digest, filename, requirements, python_spec, item["requested"])


def _closure(packages: dict[str, LockedPackage], roots: tuple[Requirement, ...], env: dict[str, str]) -> None:
    pending = list(roots)
    visited: set[tuple[str, str]] = set()
    while pending:
        item = pending.pop()
        name = canonicalize_name(item.name)
        package = packages.get(name)
        if package is None or not item.specifier.contains(package.version, prereleases=True):
            raise LockError(f"missing or incompatible dependency: {item}")
        for extra in ("", *sorted(item.extras)):
            if (name, extra) in visited:
                continue
            visited.add((name, extra))
            if len(visited) > MAX_PACKAGES * 65:
                raise LockError("dependency traversal budget exceeded")
            for dependency in package.requirements:
                if dependency.marker is None or dependency.marker.evaluate({**env, "extra": extra}):
                    if len(pending) >= MAX_PACKAGES * 65:
                        raise LockError("dependency queue budget exceeded")
                    pending.append(dependency)
    if {name for name, _ in visited} != set(packages):
        raise LockError("report contains packages outside the required dependency closure")


def render_lock(report: dict[str, object], requirements: Path) -> str:
    """Render only complete target-compatible graphs; never infer omitted edges."""
    if report.get("version") != "1" or report.get("pip_version") != PIP_VERSION:
        raise LockError("unsupported pip report schema or pip version")
    env = windows_environment(report.get("environment"))
    installs = report.get("install")
    if not isinstance(installs, list) or not 1 <= len(installs) <= MAX_PACKAGES:
        raise LockError("missing or oversized install graph")
    packages: dict[str, LockedPackage] = {}
    for raw in installs:
        package = _package(raw, env)
        if package.name in packages:
            raise LockError("duplicate canonical package name")
        packages[package.name] = package
    roots = root_requirements(requirements, env)
    if {p.name for p in packages.values() if p.requested} != {canonicalize_name(r.name) for r in roots}:
        raise LockError("requested package set differs from root requirements")
    _closure(packages, roots, env)
    lines = ["# WaveHelm resolved-artifact hash lock v1",
             f"# Target: Windows x64 / CPython {env['python_version']}",
             "# Scope: resolved graph; NOT an installed-graph audit or vulnerability result.",
             "# Human review: REQUIRED before integration.",
             "--only-binary=:all:"]
    for name in sorted(packages):
        p = packages[name]
        lines.append(f"{p.name}=={p.version} --hash=sha256:{p.sha256}")
    return "\n".join(lines) + "\n"


def verify_lock(lock: Path, report: Path, requirements: Path) -> str:
    expected = render_lock(read_report(report), requirements).encode("utf-8")
    if lock.is_symlink() or not lock.is_file() or lock.stat().st_size > MAX_BYTES:
        raise LockError("lock missing, linked or oversized")
    actual = lock.read_bytes()
    if actual != expected:
        raise LockError("lock bytes differ from validated graph and roots")
    return hashlib.sha256(actual).hexdigest()
