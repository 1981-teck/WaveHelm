from __future__ import annotations

from collections.abc import Mapping, Sequence
import os
import sys
from pathlib import Path

from src.config.app_metadata import get_app_name
from src.utils.helpers import resolve_user_config_root

PYCACHE_ENV_VAR = "PYTHONPYCACHEPREFIX"
UI_BACKEND_FLAG = "--ui-backend"


def _load_app_name(project_root: Path | None = None) -> str:
    root = Path(project_root) if project_root is not None else Path(__file__).resolve().parent
    info_path = root / "src" / "config" / "app_info.json"
    return get_app_name(info_path)


def _resolve_runtime_base_dir(
    app_name: str,
    *,
    os_name: str | None = None,
    sys_platform: str | None = None,
    env: Mapping[str, str] | None = None,
    home_dir: Path | None = None,
) -> Path:
    base_root = resolve_user_config_root(
        os_name=os_name,
        sys_platform=sys_platform,
        env=env,
        home_dir=home_dir,
    )
    return base_root / app_name


def configure_python_cache(
    project_root: Path | None = None,
    *,
    os_name: str | None = None,
    sys_platform: str | None = None,
    env: Mapping[str, str] | None = None,
    home_dir: Path | None = None,
) -> Path:
    app_name = _load_app_name(project_root)
    pycache_dir = _resolve_runtime_base_dir(
        app_name,
        os_name=os_name,
        sys_platform=sys_platform,
        env=env,
        home_dir=home_dir,
    ) / "pycache"

    try:
        pycache_dir.mkdir(parents=True, exist_ok=True)
        os.environ[PYCACHE_ENV_VAR] = str(pycache_dir)
        sys.pycache_prefix = str(pycache_dir)
    except OSError as error:
        print(f"[BOOT] Failed to configure Python cache directory: {error}", file=sys.stderr)

    return pycache_dir


def _consume_ui_backend_arg(argv: Sequence[str]) -> tuple[list[str], str | None]:
    """Return argv without backend flags and the requested backend name.

    Edge cases handled deterministically:
    1. ``--ui-backend`` without a following value raises ``ValueError``.
    2. Duplicate backend flags raise ``ValueError`` to avoid ambiguous startup.
    3. Only ``wx`` is accepted; any other value fails fast.
    """
    cleaned_argv: list[str] = []
    requested_backend: str | None = None
    index = 0

    while index < len(argv):
        current = argv[index]
        if current == UI_BACKEND_FLAG:
            if index + 1 >= len(argv):
                raise ValueError("Missing value for --ui-backend.")
            if requested_backend is not None:
                raise ValueError("Duplicate --ui-backend arguments are not allowed.")
            requested_backend = argv[index + 1]
            index += 2
            continue
        if current.startswith(f"{UI_BACKEND_FLAG}="):
            if requested_backend is not None:
                raise ValueError("Duplicate --ui-backend arguments are not allowed.")
            requested_backend = current.split("=", 1)[1]
            index += 1
            continue
        cleaned_argv.append(current)
        index += 1

    if requested_backend is None:
        return cleaned_argv, None

    normalized_backend = requested_backend.strip().lower()
    if not normalized_backend:
        raise ValueError('UI backend cannot be blank.')
    if normalized_backend != 'wx':
        raise ValueError('Only the wx backend is supported.')
    return cleaned_argv, normalized_backend



def main(argv: list[str] | None = None) -> int:
    configure_python_cache()

    raw_argv = list(argv) if argv is not None else list(sys.argv)
    app_argv, requested_backend = _consume_ui_backend_arg(raw_argv)

    from src.boot.runtime_bootstrap import run_application

    return run_application(app_argv, ui_backend=requested_backend)


if __name__ == "__main__":
    raise SystemExit(main())
