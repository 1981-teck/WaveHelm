"""Synthetic graph fixtures exercise comparison, never claim native installation."""
from __future__ import annotations

import copy
import json
from pathlib import Path

from packaging.markers import default_environment
import pytest

from tools.dependency_lock import LockError, render_lock
from tools.verify_windows_install import verify_installation


def fixture_files(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    env = default_environment()
    env.update(platform_system="Windows", platform_machine="AMD64", os_name="nt",
               sys_platform="win32", python_version="3.12", python_full_version="3.12.9",
               implementation_name="cpython", platform_python_implementation="CPython")
    rows = []
    for name, dependencies in (("alpha", ["beta==1.0"]), ("beta", [])):
        rows.append({"metadata": {"name": name, "version": "1.0", "requires_dist": dependencies},
                     "is_direct": False, "is_yanked": False, "requested": name == "alpha",
                     "download_info": {"url": f"https://files.pythonhosted.org/{name}-1.0-py3-none-any.whl",
                                       "archive_info": {"hashes": {"sha256": "a" * 64}}}})
    report = {"version": "1", "pip_version": "26.2.1", "environment": env, "install": rows}
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("alpha==1.0\n", encoding="utf-8")
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "pip-report.json").write_text(json.dumps(report), encoding="utf-8")
    (bundle / "requirements.lock").write_bytes(render_lock(report, requirements).encode("utf-8"))
    installed = copy.deepcopy(report)
    for package in installed["install"]:
        package["requested"] = True
    installation = tmp_path / "install.json"
    installation.write_text(json.dumps(installed), encoding="utf-8")
    live = tmp_path / "live.json"
    live.write_text(json.dumps({"schema": "wavehelm-live-inventory-v1", "platform": "win32",
        "implementation": "CPython", "bits": 64, "version": "3.12", "isolated": True,
        "prefix": "C:/test/private", "base_prefix": "C:/Python312", "system_site_packages": False,
        "packages": [{"name": name, "version": "1.0"} for name in ("alpha", "beta")]}), encoding="utf-8")
    return requirements, bundle, installation, live


def test_equal_installation_accepts_explicit_transitive_lock_entries(tmp_path: Path) -> None:
    result = verify_installation(*fixture_files(tmp_path), "3.12")
    assert result["packages"] == 2
    assert result["status"] == "INSTALLED_GRAPH_MATCH_NOT_AUDITED"
    assert result["vulnerability_status"] == "NOT_RUN"


@pytest.mark.parametrize(("case", "message"), [
    ("missing", "package set differs"), ("extra", "package set differs"),
    ("duplicate", "duplicate installed package"), ("hash", "installed artifact differs"),
    ("filename", "installed artifact differs"), ("pip", "schema/pip version differs"),
    ("platform", "native Windows CPython"), ("patch-version", "environment differ"),
    ("yanked", "package provenance"), ("direct", "package provenance"),
])
def test_installation_drift_fails(tmp_path: Path, case: str, message: str) -> None:
    files = fixture_files(tmp_path)
    installation = files[2]
    report = json.loads(installation.read_text(encoding="utf-8"))
    rows = report["install"]
    if case == "missing":
        rows.pop()
    elif case == "extra":
        item = copy.deepcopy(rows[1])
        item["metadata"]["name"] = "gamma"
        item["download_info"]["url"] = "https://files.pythonhosted.org/gamma-1.0-py3-none-any.whl"
        rows.append(item)
    elif case == "duplicate":
        rows.append(copy.deepcopy(rows[1]))
    elif case == "hash":
        rows[0]["download_info"]["archive_info"]["hashes"]["sha256"] = "b" * 64
    elif case == "filename":
        rows[0]["download_info"]["url"] = "https://files.pythonhosted.org/alpha-1.0-1-py3-none-any.whl"
    elif case == "pip":
        report["pip_version"] = "25.0"
    elif case == "platform":
        report["environment"]["platform_system"] = "Linux"
    elif case == "patch-version":
        report["environment"]["python_full_version"] = "3.12.10"
    elif case == "yanked":
        rows[0]["is_yanked"] = True
    elif case == "direct":
        rows[0]["is_direct"] = True
    installation.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(LockError, match=message):
        verify_installation(*files, "3.12")


@pytest.mark.parametrize(("case", "message"), [
    ("missing", "live packages differ"), ("seed-package", "live packages differ"),
    ("duplicate", "duplicate live package"), ("version", "live packages differ"),
    ("prefix", "private virtual environment"), ("site", "private virtual environment"),
    ("not-isolated", "target/isolation differs"), ("bits", "target/isolation differs"),
    ("target", "target/isolation differs"), ("schema", "invalid live inventory schema"),
    ("empty", "invalid live package inventory"),
])
def test_live_environment_drift_fails(tmp_path: Path, case: str, message: str) -> None:
    files = fixture_files(tmp_path)
    live = files[3]
    data = json.loads(live.read_text(encoding="utf-8"))
    if case == "missing":
        data["packages"].pop()
    elif case == "seed-package":
        data["packages"].append({"name": "pip", "version": "26.2.1"})
    elif case == "duplicate":
        data["packages"].append(data["packages"][0])
    elif case == "version":
        data["packages"][0]["version"] = "9.0"
    elif case == "prefix":
        data["prefix"] = data["base_prefix"]
    elif case == "site":
        data["system_site_packages"] = True
    elif case == "not-isolated":
        data["isolated"] = False
    elif case == "bits":
        data["bits"] = "64"
    elif case == "target":
        data["version"] = "3.13"
    elif case == "schema":
        data["schema"] = "unknown"
    elif case == "empty":
        data["packages"] = []
    live.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(LockError, match=message):
        verify_installation(*files, "3.12")


def test_lock_tamper_and_wrong_requested_target_fail(tmp_path: Path) -> None:
    files = fixture_files(tmp_path)
    with pytest.raises(LockError):
        verify_installation(*files, "3.11")
    (files[1] / "requirements.lock").write_text("alpha==1.0\n", encoding="utf-8")
    with pytest.raises(LockError):
        verify_installation(*files, "3.12")


def test_comparison_rejects_input_change_during_validation(tmp_path: Path, monkeypatch) -> None:
    from tools import verify_windows_install as module
    files = fixture_files(tmp_path)
    original = module.installed_inventory

    def changed(raw: dict[str, object], target: str) -> dict[str, str]:
        result = original(raw, target)
        files[2].write_bytes(files[2].read_bytes() + b" ")
        return result

    monkeypatch.setattr(module, "installed_inventory", changed)
    with pytest.raises(LockError, match="inputs changed"):
        module.verify_installation(*files, "3.12")
