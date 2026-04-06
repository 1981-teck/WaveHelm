from __future__ import annotations

import logging
from importlib import resources
from pathlib import Path
from typing import Iterable

from src.utils.app_paths import get_app_data_dir

logger = logging.getLogger(__name__)

RESOURCE_EXCEPTIONS = (
    AttributeError,
    FileNotFoundError,
    ModuleNotFoundError,
    OSError,
    RuntimeError,
    TypeError,
    ValueError,
)

_NOTICE_FILES = (
    "THIRD_PARTY_NOTICES.md",
    "NOTICE_INDEX.json",
    "README.md",
    "GPL_SECTION7_ADDITIONAL_TERMS.md",
)

_LICENSE_TEXT_FILES = (
    "Apache-2.0.txt",
    "BSD-3-Clause.txt",
    "LGPL-2.1.txt",
    "MIT.txt",
    "MIT-CMU.txt",
    "PSF-2.0.txt",
    "wxWindows-Library-Licence-3.1.txt",
)

_SOURCE_OFFER_TEMPLATE = """WaveHelm LGPL source-offer checklist

This file is a practical reminder for the final Microsoft Store build.
It is not legal advice and it is not a substitute for collecting the exact
upstream source archives and notices for the exact binaries you redistribute.

Before shipping the final build, complete at least these actions:

1. Freeze the exact wheel / binary versions bundled in the release.
2. Archive the complete corresponding source code for the LGPL libraries you
   redistribute, including any modifications you make.
3. Keep the LGPL license texts and the third-party notices bundle accessible to
   end users from the installed application.
4. Preserve a relinking-friendly distribution model for LGPL libraries
   (for example, do not replace dynamic library distribution with a static-only
   packaging flow unless you are prepared to satisfy the stronger obligations).
5. Keep the wxPython/wxWidgets notice set accessible while the maintained wx runtime is shipped.
"""


def get_third_party_notices_dir(base_dir: Path | None = None) -> Path:
    base = Path(base_dir) if base_dir is not None else get_app_data_dir()
    return (base / "licenses").resolve()


def _read_resource_bytes(package: str, filename: str) -> bytes:
    resource = resources.files(package).joinpath(filename)
    return resource.read_bytes()


def _write_if_changed(path: Path, payload: bytes) -> None:
    try:
        current = path.read_bytes()
    except OSError:
        current = None
    if current == payload:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def _copy_resource_group(
    destination_dir: Path,
    *,
    package: str,
    filenames: Iterable[str],
) -> None:
    for filename in filenames:
        try:
            payload = _read_resource_bytes(package, filename)
        except RESOURCE_EXCEPTIONS:
            logger.debug("Unable to read legal notice resource %s from %s.", filename, package, exc_info=True)
            continue
        _write_if_changed(destination_dir / filename, payload)


def ensure_third_party_notices_dir(base_dir: Path | None = None) -> Path:
    destination_dir = get_third_party_notices_dir(base_dir)
    destination_dir.mkdir(parents=True, exist_ok=True)
    _copy_resource_group(
        destination_dir,
        package="src.resources.legal",
        filenames=_NOTICE_FILES,
    )
    _copy_resource_group(
        destination_dir,
        package="src.resources.legal.licenses",
        filenames=_LICENSE_TEXT_FILES,
    )
    _write_if_changed(
        destination_dir / "LGPL_SOURCE_CODE_OFFER_TEMPLATE.txt",
        _SOURCE_OFFER_TEMPLATE.encode("utf-8"),
    )
    return destination_dir
