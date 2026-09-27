from __future__ import annotations

import ast
import os
from pathlib import Path
import subprocess
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_MODULE = "src.video.component_adapter.com_thread_manager_runtime"
MANAGER_MODULE = "src.video.component_adapter.com_thread_manager"


def _run_fresh_python(script: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    existing_path = env.get("PYTHONPATH")
    env["PYTHONPATH"] = str(REPO_ROOT) if not existing_path else os.pathsep.join((str(REPO_ROOT), existing_path))
    return subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPO_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )


def _assert_fresh_python_ok(script: str) -> None:
    result = _run_fresh_python(script)
    assert result.returncode == 0, result.stdout + result.stderr


def test_runtime_module_does_not_import_owner_as_side_effect():
    _assert_fresh_python_ok(
        f'''\nimport importlib\nimport sys\nruntime = importlib.import_module("{RUNTIME_MODULE}")\nassert "{MANAGER_MODULE}" not in sys.modules\ntry:\n    runtime._home_module()\nexcept RuntimeError as exc:\n    assert "must be loaded" in str(exc)\nelse:\n    raise AssertionError("runtime helper unexpectedly imported its owner")\nassert "{MANAGER_MODULE}" not in sys.modules\n'''
    )


def test_import_runtime_then_manager_is_order_independent():
    _assert_fresh_python_ok(
        f'''\nimport importlib\nruntime = importlib.import_module("{RUNTIME_MODULE}")\nmanager = importlib.import_module("{MANAGER_MODULE}")\nassert runtime._home_module() is manager\ninstance = manager.ComThreadManager()\nassert callable(instance._is_on_com_thread)\nassert callable(instance._wake_com_thread)\nassert callable(instance._reject_pending_tasks)\nassert callable(instance._drain_tasks_com_thread)\n'''
    )


def test_import_manager_then_runtime_is_order_independent():
    _assert_fresh_python_ok(
        f'''\nimport importlib\nmanager = importlib.import_module("{MANAGER_MODULE}")\nruntime = importlib.import_module("{RUNTIME_MODULE}")\nassert runtime._home_module() is manager\ninstance = manager.ComThreadManager()\nassert callable(instance._is_on_com_thread)\nassert callable(instance._wake_com_thread)\nassert callable(instance._reject_pending_tasks)\nassert callable(instance._drain_tasks_com_thread)\n'''
    )


def test_runtime_source_has_no_owner_import_edge():
    source_path = REPO_ROOT / "src" / "video" / "component_adapter" / "com_thread_manager_runtime.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))

    imported_modules: list[str] = []
    dynamic_import_calls: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.append(node.module)
        elif isinstance(node, ast.Import):
            imported_modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                dynamic_import_calls.append(f"{func.value.id}.{func.attr}")

    assert not any(name.endswith("com_thread_manager") for name in imported_modules)
    assert "importlib.import_module" not in dynamic_import_calls
