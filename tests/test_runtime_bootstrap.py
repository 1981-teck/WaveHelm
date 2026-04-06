from __future__ import annotations

import io
import logging
import sys
from pathlib import Path
from types import SimpleNamespace
import shutil
from contextlib import redirect_stdout

from src.boot import runtime_bootstrap

RUNTIME_ROOT = Path(__file__).resolve().parent / "_bootstrap_runtime"


def _make_runtime_dir(name: str) -> Path:
    path = RUNTIME_ROOT / name
    shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True, exist_ok=True)
    return path


def test_maybe_clear_source_pycache_is_opt_in(monkeypatch):
    project_root = _make_runtime_dir("source_pycache_opt_in")
    cache_dir = project_root / "src" / "pkg" / "__pycache__"
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "module.pyc").write_text("compiled", encoding="utf-8")

    monkeypatch.delenv(runtime_bootstrap.CLEAR_SOURCE_PYCACHE_ENV_VAR, raising=False)

    assert runtime_bootstrap.maybe_clear_source_pycache(project_root) == 0
    assert cache_dir.exists()

    monkeypatch.setenv(runtime_bootstrap.CLEAR_SOURCE_PYCACHE_ENV_VAR, "1")

    assert runtime_bootstrap.maybe_clear_source_pycache(project_root) == 1
    assert not cache_dir.exists()


def test_configure_comtypes_policy_sets_multithreaded_mode(monkeypatch):
    fake_comtypes = SimpleNamespace(
        COINIT_MODE=0,
        COINIT_MULTITHREADED=987,
    )
    monkeypatch.setitem(sys.modules, "comtypes", fake_comtypes)

    runtime_bootstrap.configure_comtypes_policy()

    assert fake_comtypes.COINIT_MODE == fake_comtypes.COINIT_MULTITHREADED


def test_normalize_ui_backend_prefers_explicit_value(monkeypatch):
    monkeypatch.setenv(runtime_bootstrap.UI_BACKEND_ENV_VAR, "qt")

    assert runtime_bootstrap._normalize_ui_backend("wx") == "wx"


def test_normalize_ui_backend_rejects_unknown_backend(monkeypatch):
    monkeypatch.delenv(runtime_bootstrap.UI_BACKEND_ENV_VAR, raising=False)

    try:
        runtime_bootstrap._normalize_ui_backend("qt")
    except ValueError as error:
        assert "Supported values: wx" in str(error)
    else:  # pragma: no cover - defensive branch
        raise AssertionError("Expected ValueError for unsupported backend.")


def test_show_critical_error_dialog_falls_back_to_stdout(monkeypatch):
    class BrokenWxModule:
        OK = 1

        @staticmethod
        def MessageBox(*args, **kwargs):
            raise RuntimeError("dialog fail")

    app = runtime_bootstrap.MainApp.__new__(runtime_bootstrap.MainApp)
    app._wx = BrokenWxModule
    stdout = io.StringIO()

    with redirect_stdout(stdout):
        runtime_bootstrap.MainApp.show_critical_error_dialog(app, "boom")

    assert "Fatal error: boom" in stdout.getvalue()


def test_cleanup_resources_tolerates_typed_shutdown_failures(monkeypatch, caplog):
    app = runtime_bootstrap.MainApp.__new__(runtime_bootstrap.MainApp)
    app.app_controller = SimpleNamespace(
        shutdown=lambda: (_ for _ in ()).throw(RuntimeError("app fail"))
    )
    fake_manager = SimpleNamespace(
        shutdown=lambda: (_ for _ in ()).throw(RuntimeError("com fail"))
    )
    module = SimpleNamespace(com_thread_manager=fake_manager)
    monkeypatch.setitem(sys.modules, "src.video.component_adapter.com_thread_manager", module)

    with caplog.at_level(logging.DEBUG):
        runtime_bootstrap.MainApp.cleanup_resources(app)

    assert "AppController.shutdown() failed during wx cleanup" in caplog.text
    assert "com_thread_manager.shutdown() failed during wx cleanup" in caplog.text


def test_run_application_returns_one_on_typed_startup_failure(monkeypatch):
    monkeypatch.setattr(runtime_bootstrap, "get_app_data_dir", lambda: _make_runtime_dir("run_application_fail"))
    monkeypatch.setattr(runtime_bootstrap, "setup_logging", lambda base_dir: None)
    monkeypatch.setattr(runtime_bootstrap, "maybe_clear_source_pycache", lambda project_root=None: 0)
    monkeypatch.setattr(runtime_bootstrap, "configure_comtypes_policy", lambda: None)
    monkeypatch.setattr(runtime_bootstrap, "MainApp", lambda argv=None: (_ for _ in ()).throw(RuntimeError("startup fail")))

    assert runtime_bootstrap.run_application([]) == 1


def test_run_application_uses_wx_backend(monkeypatch):
    calls: dict[str, object] = {}

    class FakeWxMainApp:
        def __init__(self, argv=None):
            calls["argv"] = list(argv or [])

        def start(self) -> int:
            calls["started"] = True
            return 7

        def cleanup_resources(self) -> None:
            calls["cleaned"] = True

        def show_critical_error_dialog(self, message: str) -> None:
            calls["error"] = message

    monkeypatch.setattr(runtime_bootstrap, "get_app_data_dir", lambda: _make_runtime_dir("run_application_wx"))
    monkeypatch.setattr(runtime_bootstrap, "setup_logging", lambda base_dir: None)
    monkeypatch.setattr(runtime_bootstrap, "maybe_clear_source_pycache", lambda project_root=None: 0)
    monkeypatch.setattr(runtime_bootstrap, "configure_comtypes_policy", lambda: None)
    monkeypatch.setattr(runtime_bootstrap, "MainApp", FakeWxMainApp)

    assert runtime_bootstrap.run_application(["wavehelm", "--demo"], ui_backend="wx") == 7
    assert calls == {
        "argv": ["wavehelm", "--demo"],
        "started": True,
        "cleaned": True,
    }


def test_main_app_uses_canonical_metadata_identity(monkeypatch):
    metadata_dir = _make_runtime_dir("metadata_identity")
    captured: dict[str, object] = {}

    class FakeWxApp:
        def __init__(self, redirect=False):
            captured["redirect"] = redirect

        def SetAppName(self, value):
            captured["app_name"] = value

        def SetAppDisplayName(self, value):
            captured["display_name"] = value

        def SetVendorName(self, value):
            captured["organization_name"] = value

    class FakeAppController:
        def __init__(self, root, service_container, ui_backend=None):
            self.root = root
            self.service_container = service_container
            self.ui_backend = ui_backend

    fake_container = object()
    monkeypatch.setitem(sys.modules, "wx", SimpleNamespace(App=FakeWxApp, MessageBox=lambda *args, **kwargs: None, OK=1))
    monkeypatch.setitem(sys.modules, "src.controller.app_controller", SimpleNamespace(AppController=FakeAppController))
    monkeypatch.setitem(sys.modules, "src.services.service_container", SimpleNamespace(get_service_container=lambda: fake_container))
    monkeypatch.setattr(runtime_bootstrap, "get_app_general_metadata", lambda: {
        "app_name": "WaveHelm QA",
        "organization_name": "WaveHelm Org",
        "version": "9.9.9",
    })
    monkeypatch.setattr(runtime_bootstrap, "get_app_data_dir", lambda: metadata_dir)

    app = runtime_bootstrap.MainApp(["wavehelm"])

    assert captured["app_name"] == "WaveHelm QA"
    assert captured["organization_name"] == "WaveHelm Org"
    assert app.service_container is fake_container
    assert app.app_controller.ui_backend == 'wx'


def test_wx_main_app_uses_app_controller_shell(monkeypatch):
    metadata_dir = _make_runtime_dir("wx_app_controller_shell")
    captured: dict[str, object] = {}

    class FakeWxApp:
        def __init__(self, _redirect=False):
            captured["redirect"] = _redirect

        def SetAppName(self, value):
            captured["app_name"] = value

        def SetAppDisplayName(self, value):
            captured["display_name"] = value

        def SetVendorName(self, value):
            captured["vendor_name"] = value

    class FakeAppController:
        def __init__(self, root, service_container, ui_backend=None):
            captured["root"] = root
            captured["service_container"] = service_container
            captured["ui_backend"] = ui_backend
            self.initialize_calls = 0
            self.run_calls = 0
            self.shutdown_calls = 0

        def initialize_app(self):
            self.initialize_calls += 1

        def run_main_loop(self):
            self.run_calls += 1
            return 19

        def shutdown(self):
            self.shutdown_calls += 1

    fake_container = object()
    monkeypatch.setitem(sys.modules, "wx", SimpleNamespace(App=FakeWxApp, MessageBox=lambda *args, **kwargs: None, OK=1))
    monkeypatch.setitem(sys.modules, "src.controller.app_controller", SimpleNamespace(AppController=FakeAppController))
    monkeypatch.setitem(sys.modules, "src.services.service_container", SimpleNamespace(get_service_container=lambda: fake_container))
    monkeypatch.setattr(runtime_bootstrap, "get_app_general_metadata", lambda: {
        "app_name": "WaveHelm WX",
        "organization_name": "WaveHelm Org",
        "version": "1.2.3",
    })
    monkeypatch.setattr(runtime_bootstrap, "get_app_data_dir", lambda: metadata_dir)

    app = runtime_bootstrap.WxMainApp(["wavehelm", "--ui-backend", "wx"])
    result = app.start()
    app.cleanup_resources()

    assert result == 19
    assert captured["ui_backend"] == "wx"
    assert captured["service_container"] is fake_container
    assert captured["app_name"] == "WaveHelm WX"
    assert app.app_controller.initialize_calls == 1
    assert app.app_controller.run_calls == 1
    assert app.app_controller.shutdown_calls == 1
