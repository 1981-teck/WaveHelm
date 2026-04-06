from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path
import shutil

import main

RUNTIME_ROOT = Path(__file__).resolve().parent / "_bootstrap_runtime"


def _make_runtime_dir(name: str) -> Path:
    path = RUNTIME_ROOT / name
    shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True, exist_ok=True)
    return path


def test_importing_main_has_no_boot_side_effects(monkeypatch):
    monkeypatch.delenv("PYTHONPYCACHEPREFIX", raising=False)
    monkeypatch.setattr(sys, "pycache_prefix", None, raising=False)

    importlib.reload(main)

    assert os.environ.get("PYTHONPYCACHEPREFIX") is None
    assert getattr(sys, "pycache_prefix", None) is None


def test_configure_python_cache_sets_env_and_prefix(monkeypatch):
    runtime_dir = _make_runtime_dir("main_pycache")
    monkeypatch.setattr(main, "_load_app_name", lambda project_root=None: "WaveHelmTest")
    monkeypatch.setattr(sys, "pycache_prefix", None, raising=False)

    pycache_dir = main.configure_python_cache(
        os_name="nt",
        env={"APPDATA": str(runtime_dir)},
        home_dir=runtime_dir / "home",
    )

    assert pycache_dir == runtime_dir / "WaveHelmTest" / "pycache"
    assert os.environ["PYTHONPYCACHEPREFIX"] == str(pycache_dir)
    assert sys.pycache_prefix == str(pycache_dir)


def test_consume_ui_backend_arg_accepts_only_wx_backend():
    assert main._consume_ui_backend_arg(["wavehelm", "--ui-backend", "wx", "--demo"]) == (
        ["wavehelm", "--demo"],
        "wx",
    )
    assert main._consume_ui_backend_arg(["wavehelm", "--ui-backend=wx", "--demo"]) == (
        ["wavehelm", "--demo"],
        "wx",
    )


def test_consume_ui_backend_arg_rejects_missing_duplicate_or_unsupported_values():
    try:
        main._consume_ui_backend_arg(["wavehelm", "--ui-backend"])
    except ValueError as error:
        assert "Missing value" in str(error)
    else:  # pragma: no cover - defensive branch
        raise AssertionError("Expected ValueError for missing backend value.")

    try:
        main._consume_ui_backend_arg(["wavehelm", "--ui-backend", "wx", "--ui-backend=wx"])
    except ValueError as error:
        assert "Duplicate" in str(error)
    else:  # pragma: no cover - defensive branch
        raise AssertionError("Expected ValueError for duplicate backend values.")

    try:
        main._consume_ui_backend_arg(["wavehelm", "--ui-backend=unsupported"])
    except ValueError as error:
        assert "wx backend" in str(error)
    else:  # pragma: no cover - defensive branch
        raise AssertionError("Expected ValueError for unsupported backend.")


def test_main_forwards_selected_ui_backend(monkeypatch):
    captured: dict[str, object] = {}

    monkeypatch.setattr(main, "configure_python_cache", lambda: _make_runtime_dir("forward_backend"))

    def fake_run_application(argv, ui_backend=None):
        captured["argv"] = list(argv)
        captured["ui_backend"] = ui_backend
        return 13

    from src.boot import runtime_bootstrap

    monkeypatch.setattr(runtime_bootstrap, "run_application", fake_run_application)

    result = main.main(["wavehelm", "--ui-backend", "wx", "--demo"])

    assert result == 13
    assert captured == {
        "argv": ["wavehelm", "--demo"],
        "ui_backend": "wx",
    }


def test_resolve_runtime_base_dir_supports_injected_platform_context():
    runtime_dir = _make_runtime_dir("main_runtime_base_dir")
    base_dir = main._resolve_runtime_base_dir(
        "WaveHelmTest",
        os_name="posix",
        sys_platform="darwin",
        env={},
        home_dir=runtime_dir / "home",
    )

    assert base_dir == (runtime_dir / "home" / "Library" / "Application Support" / "WaveHelmTest")
