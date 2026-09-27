"""Generated metadata fixtures test the validator, never claim live resolution."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import subprocess
import sys

from packaging.markers import default_environment
import pytest

from tools.dependency_lock import LockError, read_report, render_lock, verify_lock


def _report() -> dict:
    env = default_environment()
    env.update(platform_system="Windows", platform_machine="AMD64", os_name="nt",
               sys_platform="win32", python_version="3.12", python_full_version="3.12.9",
               implementation_name="cpython", platform_python_implementation="CPython")
    records = []
    for name, version, requires in [("alpha", "1.0", ["beta>=2; platform_system == 'Windows'"]),
                                    ("beta", "2.0", [])]:
        records.append({"metadata": {"name": name, "version": version, "requires_dist": requires,
                                     "requires_python": ">=3.11"},
                        "is_direct": False, "is_yanked": False, "requested": name == "alpha",
                        "download_info": {"url": f"https://files.pythonhosted.org/packages/{name}-{version}-py3-none-any.whl",
                                          "archive_info": {"hashes": {"sha256": "a" * 64}}}})
    return {"version": "1", "pip_version": "26.2.1", "environment": env, "install": records}


def _roots(tmp_path: Path, text: str = "alpha==1.0\n") -> Path:
    path = tmp_path / "requirements.txt"
    path.write_text(text, encoding="utf-8")
    return path


def test_complete_windows_graph_renders_deterministic_hashes(tmp_path: Path) -> None:
    roots = _roots(tmp_path)
    a = _report()
    b = copy.deepcopy(a)
    b["install"].reverse()
    assert render_lock(a, roots) == render_lock(b, roots)
    lock = render_lock(a, roots)
    assert "beta==2.0 --hash=sha256:" in lock
    assert "NOT an installed-graph audit" in lock
    assert "Human review: REQUIRED" in lock
    assert "reviewed-artifact" not in lock


@pytest.mark.parametrize("field,value", [("platform_system", "Linux"), ("platform_machine", "ARM64"),
    ("python_version", "3.10"), ("python_full_version", "3.11.8"), ("os_name", "posix"),
    ("sys_platform", "linux"), ("implementation_name", "pypy")])
def test_wrong_or_inconsistent_platform_is_rejected(tmp_path: Path, field: str, value: str) -> None:
    report = _report()
    report["environment"][field] = value
    with pytest.raises(LockError):
        render_lock(report, _roots(tmp_path))


@pytest.mark.parametrize("case", ["missing", "incompatible", "duplicate", "extra", "requested",
    "unhashed", "unsafe-url", "foreign-wheel", "yanked", "direct", "bad-python", "bad-pip"])
def test_invalid_graphs_cannot_be_promoted(tmp_path: Path, case: str) -> None:
    report = _report()
    alpha, beta = report["install"]
    if case == "missing":
        report["install"].pop()
    elif case == "incompatible":
        alpha["metadata"]["requires_dist"] = ["beta>=3"]
    elif case == "duplicate":
        report["install"].append(copy.deepcopy(beta))
    elif case == "extra":
        alpha["metadata"]["requires_dist"] = []
    elif case == "requested":
        beta["requested"] = True
    elif case == "unhashed":
        alpha["download_info"]["archive_info"]["hashes"] = {}
    elif case == "unsafe-url":
        alpha["download_info"]["url"] = "http://evil.invalid/alpha-1.0-py3-none-any.whl"
    elif case == "foreign-wheel":
        alpha["download_info"]["url"] = "https://files.pythonhosted.org/alpha-1.0-cp312-cp312-manylinux2014_x86_64.whl"
    elif case == "yanked":
        alpha["is_yanked"] = True
    elif case == "direct":
        alpha["is_direct"] = True
    elif case == "bad-python":
        alpha["metadata"]["requires_python"] = ">=3.13"
    elif case == "bad-pip":
        report["pip_version"] = "25.0"
    with pytest.raises(LockError):
        render_lock(report, _roots(tmp_path))


@pytest.mark.parametrize("root", ["alpha>=1", "alpha==1.*", "alpha @ https://example.invalid/x.whl",
                                  "alpha==1.0\nAlpha==1.0", "-r requirements.txt"])
def test_root_pins_and_include_cycles_are_fail_closed(tmp_path: Path, root: str) -> None:
    with pytest.raises(LockError):
        render_lock(_report(), _roots(tmp_path, root))


def test_transitive_extra_dependency_and_cycle_are_resolved(tmp_path: Path) -> None:
    report = _report()
    alpha, beta = report["install"]
    alpha["metadata"]["requires_dist"] = ["beta[feature]>=2"]
    beta["metadata"]["requires_dist"] = ["alpha==1.0; extra == 'feature'"]
    assert "alpha==1.0" in render_lock(report, _roots(tmp_path))


def test_hash_lock_rejects_byte_tampering_and_root_drift(tmp_path: Path) -> None:
    roots = _roots(tmp_path)
    report = tmp_path / "report.json"
    report.write_text(json.dumps(_report()), encoding="utf-8")
    lock = tmp_path / "requirements.lock"
    lock.write_bytes(render_lock(_report(), roots).encode("utf-8"))
    assert len(verify_lock(lock, report, roots)) == 64
    lock.write_text(lock.read_text(encoding="utf-8") + "gamma==4 --hash=sha256:" + "a" * 64, encoding="utf-8")
    with pytest.raises(LockError, match="bytes differ"):
        verify_lock(lock, report, roots)
    roots.write_text("alpha==2.0\n", encoding="utf-8")
    with pytest.raises(LockError):
        verify_lock(lock, report, roots)


def test_duplicate_json_keys_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "report.json"
    path.write_text('{"version":"1","version":"2"}', encoding="utf-8")
    with pytest.raises(LockError, match="duplicate"):
        read_report(path)


def test_non_windows_cli_fails_without_creating_lock_bundle(tmp_path: Path) -> None:
    if sys.platform == "win32":
        pytest.skip("foreign-platform rejection test")
    root = Path(__file__).resolve().parents[1]
    output = tmp_path / "bundle"
    command = [sys.executable, "-B", str(root / "tools/resolve_windows_lock.py"), "resolve",
               "--requirements", str(_roots(tmp_path)), "--bundle", str(output)]
    result = subprocess.run(command, text=True, capture_output=True, timeout=15, check=False, encoding="utf-8", errors="strict")
    assert result.returncode == 1
    assert "native Windows" in result.stdout
    assert not output.exists()


def test_missing_marker_environment_cannot_fall_back_to_host(tmp_path: Path) -> None:
    report = _report()
    del report["environment"]["platform_release"]
    with pytest.raises(LockError, match="marker fields"):
        render_lock(report, _roots(tmp_path))


def test_hash_lock_rejects_crlf_translation(tmp_path: Path) -> None:
    """Canonical bytes must not be silently normalized by a verifier."""
    roots = _roots(tmp_path)
    report = tmp_path / "report.json"
    report.write_bytes(json.dumps(_report()).encode("utf-8"))
    lock = tmp_path / "requirements.lock"
    canonical = render_lock(_report(), roots).encode("utf-8")
    lock.write_bytes(canonical)
    assert len(verify_lock(lock, report, roots)) == 64
    lock.write_bytes(canonical.replace(b"\n", b"\r\n"))
    with pytest.raises(LockError, match="bytes differ"):
        verify_lock(lock, report, roots)
