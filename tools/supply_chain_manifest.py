"""Create and verify no-follow manifests for WaveHelm supply-chain evidence."""

from __future__ import annotations

from pathlib import Path

from tools.supply_chain_contract import (
    MANIFEST_SCHEMA,
    Policy,
    SupplyChainError,
    read_json,
    sha256_file,
)

MAX_EVIDENCE_FILES = 256
MAX_FINDINGS = 2_000
VERIFY_SCHEMA = "wavehelm-supply-chain-manifest-verify-v1"


def _append(findings: list[str], value: str) -> None:
    if len(findings) < MAX_FINDINGS:
        findings.append(value)
    elif len(findings) == MAX_FINDINGS:
        findings.append("finding_limit_exceeded")


def _require_directory(evidence: Path) -> Path:
    if evidence.is_symlink():
        raise SupplyChainError("evidence directory must not be a symlink")
    try:
        resolved = evidence.resolve(strict=True)
    except OSError as exc:
        raise SupplyChainError(f"cannot resolve evidence directory: {exc}") from exc
    if not resolved.is_dir():
        raise SupplyChainError("evidence path must be a directory")
    return resolved


def _regular_files(evidence: Path) -> list[Path]:
    files: list[Path] = []
    for path in sorted(evidence.iterdir(), key=lambda item: item.name):
        if path.is_symlink():
            raise SupplyChainError(f"evidence contains a symlink: {path.name}")
        if not path.is_file():
            raise SupplyChainError(f"evidence contains a non-file: {path.name}")
        files.append(path)
        if len(files) > MAX_EVIDENCE_FILES:
            raise SupplyChainError("evidence file count exceeds policy")
    return files


def create_manifest(evidence: Path, policy: Policy) -> dict[str, object]:
    root = _require_directory(evidence)
    entries: list[dict[str, object]] = []
    for path in _regular_files(root):
        if path.name == "supply-chain-evidence-manifest.json":
            continue
        digest, size = sha256_file(path, policy.max_file_bytes)
        entries.append({"path": path.name, "sha256": digest, "size": size})
    return {"schema": MANIFEST_SCHEMA, "files": entries}


def verify_manifest(evidence: Path, policy: Policy) -> dict[str, object]:
    root = _require_directory(evidence)
    manifest_path = root / "supply-chain-evidence-manifest.json"
    raw = read_json(manifest_path, policy.max_file_bytes)
    if type(raw) is not dict or raw.get("schema") != MANIFEST_SCHEMA:
        raise SupplyChainError("invalid evidence manifest")
    items = raw.get("files")
    if type(items) is not list or len(items) > MAX_EVIDENCE_FILES:
        raise SupplyChainError("invalid evidence manifest file list")
    findings: list[str] = []
    expected: set[str] = set()
    for item in items:
        if type(item) is not dict or set(item) != {"path", "sha256", "size"}:
            _append(findings, "manifest:item")
            continue
        relative = item["path"]
        if type(relative) is not str or Path(relative).name != relative:
            _append(findings, f"manifest:path:{relative}")
            continue
        if relative in expected:
            _append(findings, f"manifest:duplicate:{relative}")
            continue
        expected.add(relative)
        target = root / relative
        if target.is_symlink() or not target.is_file():
            _append(findings, f"manifest:missing_or_unsafe:{relative}")
            continue
        digest, size = sha256_file(target, policy.max_file_bytes)
        if digest != item["sha256"] or size != item["size"]:
            _append(findings, f"manifest:mismatch:{relative}")
    actual = {path.name for path in _regular_files(root)}
    allowed = expected | {manifest_path.name}
    for relative in sorted(actual - allowed):
        _append(findings, f"manifest:extra:{relative}")
    return {"schema": VERIFY_SCHEMA, "verdict": "PASS" if not findings else "FAIL", "findings": findings}
