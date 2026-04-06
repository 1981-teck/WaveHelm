from __future__ import annotations

import logging
import os
import shutil
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Optional

from src.boot.com_policy import (
    MAIN_THREAD_COM_POLICY_NAME,
    VIDEO_THREAD_COM_POLICY_NAME,
    apply_main_thread_comtypes_policy,
)
from src.config.app_metadata import get_app_general_metadata, get_app_name
from src.utils.app_paths import get_app_data_dir
from src.utils.legal_notices import ensure_third_party_notices_dir
from src.utils.logging_config import setup_logging

logger = logging.getLogger(__name__)

CLEAR_SOURCE_PYCACHE_ENV_VAR = "WAVEHELM_CLEAR_SOURCE_PYCACHE"
UI_BACKEND_ENV_VAR = "WAVEHELM_UI_BACKEND"
DEFAULT_UI_BACKEND = "wx"
SUPPORTED_UI_BACKENDS = frozenset({"wx"})
TRUTHY_ENV_VALUES = {"1", "true", "yes", "on"}
CRITICAL_DIALOG_EXCEPTIONS = (AttributeError, RuntimeError, TypeError, ValueError)
APP_CLEANUP_EXCEPTIONS = (AttributeError, OSError, RuntimeError, TypeError, ValueError)
APP_STARTUP_EXCEPTIONS = (AttributeError, ImportError, OSError, RuntimeError, TypeError, ValueError)


def _ensure_app_dirs(base_dir: Path) -> None:
    """Create the minimum runtime folders required during startup."""
    (base_dir / "logs").mkdir(parents=True, exist_ok=True)
    ensure_third_party_notices_dir(base_dir)


def _is_truthy_env(value: str | None) -> bool:
    return isinstance(value, str) and value.strip().lower() in TRUTHY_ENV_VALUES


def _normalize_ui_backend(
    requested_backend: str | None,
    *,
    env: Mapping[str, str] | None = None,
) -> str:
    """Resolve the UI backend name with explicit-arg priority.

    Edge cases handled deterministically:
    1. Explicit CLI/backend-selector values override environment configuration.
    2. Blank values are rejected to avoid silent backend drift.
    3. Unsupported backend names raise ``ValueError`` before GUI startup.
    """
    env_mapping = os.environ if env is None else env
    raw_backend = requested_backend if requested_backend is not None else env_mapping.get(UI_BACKEND_ENV_VAR)
    normalized = (raw_backend or DEFAULT_UI_BACKEND).strip().lower()
    if not normalized:
        raise ValueError("UI backend cannot be blank.")
    if normalized not in SUPPORTED_UI_BACKENDS:
        raise ValueError("Unsupported UI backend '" + normalized + "'. Supported values: wx.")
    return normalized


def clear_legacy_source_pycache(project_root: Path | None = None) -> int:
    """Remove legacy ``__pycache__`` folders from the source tree on demand."""
    root = Path(project_root) if project_root is not None else Path(__file__).resolve().parents[2]
    src_dir = root / "src"
    if not src_dir.is_dir():
        return 0

    removed = 0
    for item in src_dir.rglob("__pycache__"):
        if not item.is_dir():
            continue
        shutil.rmtree(item, ignore_errors=True)
        removed += 1
    return removed


def maybe_clear_source_pycache(project_root: Path | None = None) -> int:
    """Consume the source-pycache cleanup request at most once per process."""
    env_value = os.environ.pop(CLEAR_SOURCE_PYCACHE_ENV_VAR, None)
    if not _is_truthy_env(env_value):
        return 0

    removed = clear_legacy_source_pycache(project_root)
    logger.info("Removed %d legacy source __pycache__ directories.", removed)
    return removed


def configure_comtypes_policy() -> None:
    """Configure the COM policy before importing UI/runtime modules."""
    apply_main_thread_comtypes_policy()
    logger.debug(
        "Configured main-thread comtypes policy=%s; dedicated video COM thread policy=%s.",
        MAIN_THREAD_COM_POLICY_NAME,
        VIDEO_THREAD_COM_POLICY_NAME,
    )


class WxMainApp:
    def __init__(self, argv: Sequence[str] | None = None) -> None:
        import wx

        from src.controller.app_controller import AppController
        from src.services.service_container import get_service_container

        app_general = get_app_general_metadata()
        self.base_dir = get_app_data_dir()
        _ensure_app_dirs(self.base_dir)
        self._wx = wx
        self.wx_app = wx.App(False)
        self._configure_application_metadata(app_general)
        self.service_container = get_service_container()
        self.app_controller = AppController(
            root=self.wx_app,
            service_container=self.service_container,
            ui_backend='wx',
        )

    def _configure_application_metadata(self, app_general: dict[str, str]) -> None:
        if hasattr(self.wx_app, "SetAppName"):
            self.wx_app.SetAppName(app_general["app_name"])
        if hasattr(self.wx_app, "SetAppDisplayName"):
            self.wx_app.SetAppDisplayName(app_general["app_name"])
        if hasattr(self.wx_app, "SetVendorName"):
            self.wx_app.SetVendorName(app_general["organization_name"])

    def start(self) -> int:
        """Initialize the wx shell and enter the wx event loop."""
        self.app_controller.initialize_app()
        return self.app_controller.run_main_loop()

    def show_critical_error_dialog(self, message: str) -> None:
        """Show a fatal-error dialog using wx with a stdout fallback."""
        try:
            self._wx.MessageBox(message, f"{get_app_name()} - Fatal Error", style=getattr(self._wx, "OK", 0))
        except CRITICAL_DIALOG_EXCEPTIONS:
            print(f"Fatal error: {message}")

    def cleanup_resources(self) -> None:
        """Perform an orderly shutdown for wx services."""
        try:
            self.app_controller.shutdown()
        except APP_CLEANUP_EXCEPTIONS:
            logging.debug("AppController.shutdown() failed during wx cleanup (best effort).", exc_info=True)

        try:
            from src.video.component_adapter.com_thread_manager import com_thread_manager
            com_thread_manager.shutdown()
            logging.info("Video COM thread manager shut down successfully.")
        except APP_CLEANUP_EXCEPTIONS:
            logging.debug("com_thread_manager.shutdown() failed during wx cleanup (best effort).", exc_info=True)


MainApp = WxMainApp


def run_application(
    argv: Sequence[str] | None = None,
    *,
    ui_backend: str | None = None,
) -> int:
    base_dir = get_app_data_dir()
    _ensure_app_dirs(base_dir)
    setup_logging(base_dir)
    maybe_clear_source_pycache()
    configure_comtypes_policy()

    app: Optional[Any] = None
    exit_code = 0

    try:
        _normalize_ui_backend(ui_backend)
        app = MainApp(argv)
        exit_code = int(app.start() or 0)
    except APP_STARTUP_EXCEPTIONS as error:
        logging.critical("Fatal error during application startup", exc_info=True)
        if app is not None:
            app.show_critical_error_dialog(str(error))
        else:
            print(f"Fatal error: {error}")
        exit_code = 1
    finally:
        if app is not None:
            app.cleanup_resources()

    return exit_code
