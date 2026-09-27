"""Deterministic source hygiene and cleanup gate for WaveHelm."""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

# These command-line tools must not contaminate the source tree with bytecode.
sys.dont_write_bytecode = True

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.source_hygiene_fs import HygieneError, cleanup

POLICY_SCHEMA = "wavehelm-source-hygiene-policy-v1"
REPORT_SCHEMA = "wavehelm-source-hygiene-report-v1"
ACTION_PIN_PATTERN = re.compile(r"^[0-9a-fA-F]{40}$")
USES_PATTERN = re.compile(r"^\s*-?\s*uses\s*:\s*([^\s#]+)")
MAX_REPORT_FINDINGS = 2_000
TEXT_SUFFIXES = frozenset({
    ".css", ".csv", ".diff", ".html", ".ini", ".json", ".md", ".patch",
    ".py", ".pyi", ".rst", ".svg", ".toml", ".txt", ".xml", ".yaml", ".yml",
})
TEXT_NAMES = frozenset({"LICENSE", "MANIFEST.in", "NOTICE", "SECURITY.md"})


@dataclass(frozen=True)
class Policy:
    max_files: int
    max_total_bytes: int
    max_file_lines: int
    max_function_statements: int
    legacy_oversize_files: dict[str, int]
    legacy_oversize_functions: dict[str, int]
    legacy_mixed_line_endings: frozenset[str]
    legacy_runtime_asserts: frozenset[str]
    required_paths: tuple[str, ...]
    allowed_untracked_paths: frozenset[str]
    forbidden_parts: frozenset[str]
    forbidden_suffixes: frozenset[str]


@dataclass(frozen=True)
class FunctionMetric:
    path: str
    qualname: str
    statements: int
    line: int


class FunctionVisitor(ast.NodeVisitor):
    """Collect qualified function names and recursive statement counts."""

    def __init__(self, path: str) -> None:
        self.path = path
        self.scope: list[str] = []
        self.metrics: list[FunctionMetric] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._record_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._record_function(node)

    def _record_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        qualname = ".".join((*self.scope, node.name))
        statements = _count_statements(node.body)
        self.metrics.append(FunctionMetric(self.path, qualname, statements, node.lineno))
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()


def _count_statements(nodes: Sequence[ast.stmt]) -> int:
    count = 0
    stack: list[ast.AST] = list(nodes)
    while stack:
        node = stack.pop()
        if isinstance(node, ast.stmt):
            count += 1
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                continue
            stack.append(child)
    return count


def _exact_int(value: object, name: str, minimum: int) -> int:
    if type(value) is not int or value < minimum:
        raise HygieneError(f"{name} must be an integer >= {minimum}")
    return value


def _string_map(value: object, name: str) -> dict[str, int]:
    if type(value) is not dict:
        raise HygieneError(f"{name} must be an object")
    result: dict[str, int] = {}
    for key, item in value.items():
        if type(key) is not str or not key:
            raise HygieneError(f"{name} contains an invalid key")
        result[key] = _exact_int(item, f"{name}.{key}", 1)
    return result


def _string_sequence(value: object, name: str) -> tuple[str, ...]:
    if type(value) is not list:
        raise HygieneError(f"{name} must be an array")
    result: list[str] = []
    for item in value:
        if type(item) is not str or not item:
            raise HygieneError(f"{name} contains an invalid item")
        result.append(item)
    if len(result) != len(set(result)):
        raise HygieneError(f"{name} contains duplicates")
    return tuple(result)


def load_policy(path: Path) -> Policy:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise HygieneError(f"cannot read policy {path}: {exc}") from exc
    if type(raw) is not dict or raw.get("schema") != POLICY_SCHEMA:
        raise HygieneError("unsupported source hygiene policy schema")
    return Policy(
        max_files=_exact_int(raw.get("max_files"), "max_files", 1),
        max_total_bytes=_exact_int(raw.get("max_total_bytes"), "max_total_bytes", 1),
        max_file_lines=_exact_int(raw.get("max_file_lines"), "max_file_lines", 1),
        max_function_statements=_exact_int(
            raw.get("max_function_statements"), "max_function_statements", 1
        ),
        legacy_oversize_files=_string_map(
            raw.get("legacy_oversize_files"), "legacy_oversize_files"
        ),
        legacy_oversize_functions=_string_map(
            raw.get("legacy_oversize_functions"), "legacy_oversize_functions"
        ),
        legacy_mixed_line_endings=frozenset(
            _string_sequence(raw.get("legacy_mixed_line_endings"), "legacy_mixed_line_endings")
        ),
        legacy_runtime_asserts=frozenset(
            _string_sequence(raw.get("legacy_runtime_asserts"), "legacy_runtime_asserts")
        ),
        required_paths=_string_sequence(raw.get("required_paths"), "required_paths"),
        allowed_untracked_paths=frozenset(
            _string_sequence(raw.get("allowed_untracked_paths"), "allowed_untracked_paths")
        ),
        forbidden_parts=frozenset(_string_sequence(raw.get("forbidden_parts"), "forbidden_parts")),
        forbidden_suffixes=frozenset(
            item.lower() for item in _string_sequence(raw.get("forbidden_suffixes"), "forbidden_suffixes")
        ),
    )


def _git_tracked(root: Path) -> frozenset[str] | None:
    if not (root / ".git").exists():
        return None
    try:
        output = subprocess.check_output(
            ["git", "-C", str(root), "ls-files", "-z"], stderr=subprocess.STDOUT
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise HygieneError(f"cannot enumerate tracked files: {exc}") from exc
    return frozenset(item.decode("utf-8") for item in output.split(b"\0") if item)


def discover_files(root: Path, max_files: int, max_total_bytes: int) -> tuple[list[Path], int]:
    files: list[Path] = []
    total_bytes = 0
    stack = [root]
    while stack:
        directory = stack.pop()
        try:
            entries = list(os.scandir(directory))
        except OSError as exc:
            raise HygieneError(f"cannot scan {directory}: {exc}") from exc
        for entry in entries:
            if entry.name == ".git" and directory == root:
                continue
            path = Path(entry.path)
            try:
                if entry.is_symlink():
                    size = entry.stat(follow_symlinks=False).st_size
                elif entry.is_dir(follow_symlinks=False):
                    stack.append(path)
                    continue
                elif entry.is_file(follow_symlinks=False):
                    size = entry.stat(follow_symlinks=False).st_size
                else:
                    raise HygieneError(f"unsupported filesystem object: {path}")
            except OSError as exc:
                raise HygieneError(f"cannot inspect {path}: {exc}") from exc
            files.append(path)
            total_bytes += size
            if len(files) > max_files:
                raise HygieneError("source file count exceeds policy")
            if total_bytes > max_total_bytes:
                raise HygieneError("source byte count exceeds policy")
    files.sort(key=lambda item: item.relative_to(root).as_posix())
    return files, total_bytes


def _is_generated(relative: str, policy: Policy) -> bool:
    path = Path(relative)
    if any(part in policy.forbidden_parts for part in path.parts):
        return True
    if path.suffix.lower() in policy.forbidden_suffixes:
        return True
    if path.name.endswith(".egg-info"):
        return True
    if len(path.parts) >= 2 and path.parts[0] == "tests" and path.parts[1].startswith("_"):
        return True
    return False


def _is_text_file(path: Path) -> bool:
    return path.name in TEXT_NAMES or path.suffix.lower() in TEXT_SUFFIXES


def _read_python(path: Path) -> tuple[str, bytes]:
    try:
        data = path.read_bytes()
        return data.decode("utf-8"), data
    except (OSError, UnicodeError) as exc:
        raise HygieneError(f"cannot decode Python file {path}: {exc}") from exc


def _python_metrics(root: Path, path: Path) -> tuple[int, list[FunctionMetric], bool, list[str]]:
    relative = path.relative_to(root).as_posix()
    text, data = _read_python(path)
    line_count = len(text.splitlines())
    mixed = b"\r\n" in data and b"\n" in data.replace(b"\r\n", b"")
    try:
        tree = ast.parse(text, filename=relative)
    except SyntaxError as exc:
        return line_count, [], mixed, [f"syntax:{relative}:{exc.lineno}:{exc.msg}"]
    visitor = FunctionVisitor(relative)
    visitor.visit(tree)
    assertions = [
        f"{relative}:{node.lineno}" for node in ast.walk(tree) if isinstance(node, ast.Assert)
    ]
    return line_count, visitor.metrics, mixed, assertions


def _check_workflows(root: Path) -> list[str]:
    findings: list[str] = []
    workflow_root = root / ".github" / "workflows"
    if not workflow_root.is_dir():
        return ["missing:.github/workflows"]
    for path in sorted(workflow_root.glob("*.y*ml")):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError) as exc:
            findings.append(f"workflow_read:{path.relative_to(root).as_posix()}:{exc}")
            continue
        for number, line in enumerate(lines, 1):
            match = USES_PATTERN.match(line)
            if match is None:
                continue
            value = match.group(1).strip("'\"")
            if value.startswith("./"):
                continue
            if "@" not in value:
                findings.append(f"action_unpinned:{path.relative_to(root)}:{number}:{value}")
                continue
            revision = value.rsplit("@", 1)[1]
            if ACTION_PIN_PATTERN.fullmatch(revision) is None:
                findings.append(f"action_unpinned:{path.relative_to(root)}:{number}:{value}")
    return findings


def _append(findings: list[str], value: str) -> None:
    if len(findings) < MAX_REPORT_FINDINGS:
        findings.append(value)
    elif len(findings) == MAX_REPORT_FINDINGS:
        findings.append("finding_limit_exceeded")


def _evaluation_state(
) -> tuple[list[str], dict[str, int], dict[str, int], set[str], set[str]]:
    return [], {}, {}, set(), set()


def _inspect_text(path: Path, relative: str, findings: list[str]) -> bool:
    if path.is_symlink():
        _append(findings, f"symlink:{relative}")
        return False
    if not _is_text_file(path):
        return False
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeError:
        _append(findings, f"utf8:{relative}")
        return False
    except OSError as exc:
        _append(findings, f"read:{relative}:{exc}")
        return False
    for line_number, line in enumerate(text.splitlines(), 1):
        if line.rstrip(" \t") != line:
            _append(findings, f"trailing_whitespace:{relative}:{line_number}")
    return True


def evaluate(root: Path, policy: Policy) -> dict[str, object]:
    files, total_bytes = discover_files(root, policy.max_files, policy.max_total_bytes)
    tracked = _git_tracked(root)
    findings, actual_oversize, actual_functions, actual_mixed, actual_asserts = (
        _evaluation_state()
    )
    for required in policy.required_paths:
        if not (root / required).is_file():
            _append(findings, f"missing:{required}")
    for path in files:
        relative = path.relative_to(root).as_posix()
        if _is_generated(relative, policy):
            _append(findings, f"generated:{relative}")
        if not _inspect_text(path, relative, findings) or path.suffix != ".py":
            continue
        line_count, metrics, mixed, assertions = _python_metrics(root, path)
        if line_count > policy.max_file_lines:
            actual_oversize[relative] = line_count
        if mixed:
            actual_mixed.add(relative)
        for metric in metrics:
            if metric.statements > policy.max_function_statements:
                actual_functions[f"{metric.path}::{metric.qualname}"] = metric.statements
        if not relative.startswith("tests/"):
            actual_asserts.update(assertions)
    _compare_allowances(findings, "oversize_file", actual_oversize, policy.legacy_oversize_files)
    _compare_allowances(
        findings, "oversize_function", actual_functions, policy.legacy_oversize_functions
    )
    if actual_mixed != set(policy.legacy_mixed_line_endings):
        for item in sorted(actual_mixed - set(policy.legacy_mixed_line_endings)):
            _append(findings, f"mixed_line_endings:new:{item}")
        for item in sorted(set(policy.legacy_mixed_line_endings) - actual_mixed):
            _append(findings, f"mixed_line_endings:stale:{item}")
    _compare_allowances(
        findings,
        "runtime_assert",
        {item: 1 for item in actual_asserts},
        {item: 1 for item in policy.legacy_runtime_asserts},
    )
    for item in _check_workflows(root):
        _append(findings, item)
    if tracked is not None:
        filesystem = {path.relative_to(root).as_posix() for path in files}
        for item in sorted(tracked - filesystem):
            _append(findings, f"tracked_missing:{item}")
        for item in sorted(filesystem - tracked - policy.allowed_untracked_paths):
            _append(findings, f"untracked:{item}")
    return {
        "schema": REPORT_SCHEMA,
        "verdict": "PASS" if not findings else "FAIL",
        "root": str(root.resolve()),
        "file_count": len(files),
        "total_bytes": total_bytes,
        "git_inventory_available": tracked is not None,
        "legacy_oversize_files": len(actual_oversize),
        "legacy_oversize_functions": len(actual_functions),
        "legacy_mixed_line_endings": len(actual_mixed),
        "legacy_runtime_asserts": len(actual_asserts),
        "findings": findings,
    }


def _compare_allowances(
    findings: list[str], label: str, actual: dict[str, int], expected: dict[str, int]
) -> None:
    for key in sorted(set(actual) | set(expected)):
        if key not in expected:
            _append(findings, f"{label}:new:{key}:{actual[key]}")
        elif key not in actual:
            _append(findings, f"{label}:stale:{key}:{expected[key]}")
        elif actual[key] != expected[key]:
            _append(findings, f"{label}:changed:{key}:{expected[key]}->{actual[key]}")


def _write_json(path: Path | None, payload: dict[str, object]) -> None:
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if path is None:
        sys.stdout.write(text)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("check", "clean"))
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--policy", type=Path, default=Path("tools/source_hygiene_policy.json"))
    parser.add_argument("--output", type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    root = args.root.resolve()
    policy_path = args.policy if args.policy.is_absolute() else root / args.policy
    try:
        policy = load_policy(policy_path)
        payload = evaluate(root, policy) if args.command == "check" else cleanup(root, policy)
    except HygieneError as exc:
        payload = {"schema": REPORT_SCHEMA, "verdict": "ERROR", "error": str(exc)}
    _write_json(args.output, payload)
    return 0 if payload.get("verdict") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
