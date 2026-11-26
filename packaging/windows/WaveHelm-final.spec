# -*- mode: python ; coding: utf-8 -*-
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

PROJECT = Path(os.environ["WAVEHELM_SOURCE_ROOT"]).resolve()
ICON = Path(os.environ["WAVEHELM_ICON_FILE"]).resolve()
VERSION_FILE = Path(os.environ["WAVEHELM_VERSION_FILE"]).resolve()
INSTALLED_README = Path(os.environ["WAVEHELM_INSTALLED_README_FILE"]).resolve()
BUILD_SITE = Path(os.environ["WAVEHELM_BUILD_SITE_PACKAGES"]).resolve()
LICENSE_INVENTORY = Path(os.environ["WAVEHELM_LICENSE_INVENTORY"]).resolve()

def add_tree(src: Path, dest_root: str):
    items = []
    if not src.exists():
        return items
    for path in sorted(p for p in src.rglob("*") if p.is_file()):
        rel_parent = path.parent.relative_to(src)
        dest = Path(dest_root) / rel_parent
        items.append((str(path), str(dest).replace("\\", "/")))
    return items

datas = [
    (str(PROJECT / "src" / "config" / "app_info.json"), "src/config"),
    (str(PROJECT / "src" / "resources" / "ambient_profile.json"), "src/resources"),
    (str(PROJECT / "LICENSE"), "."),
    (str(PROJECT / "GPL_SECTION7_ADDITIONAL_TERMS.md"), "."),
    (str(PROJECT / "THIRD_PARTY_NOTICES.md"), "."),
    (str(INSTALLED_README), "."),
]
datas += add_tree(PROJECT / "src" / "locales", "src/locales")
datas += add_tree(PROJECT / "src" / "resources" / "icons", "src/resources/icons")
datas += add_tree(PROJECT / "src" / "resources" / "manual", "src/resources/manual")
datas += add_tree(PROJECT / "src" / "resources" / "legal", "src/resources/legal")
datas += add_tree(PROJECT / "ambient_sounds", "ambient_sounds")

inventory = json.loads(LICENSE_INVENTORY.read_text(encoding="utf-8"))
for package in inventory["packages"]:
    for license_file in package["license_files"]:
        relative = Path(*license_file["path"].split("/"))
        if relative.is_absolute() or ".." in relative.parts or ":" in license_file["path"]:
            raise ValueError(f"Unsafe wheel license path: {license_file['path']}")
        source = (BUILD_SITE / relative).resolve(strict=True)
        source.relative_to(BUILD_SITE)
        if hashlib.sha256(source.read_bytes()).hexdigest() != license_file["sha256"]:
            raise ValueError(f"Wheel license differs from audit inventory: {source}")
        destination = Path("src/resources/legal/licenses/wheels") / relative.parent
        datas.append((str(source), destination.as_posix()))

hiddenimports = [
    "wx",
    "matplotlib",
    "matplotlib.figure",
    "matplotlib.backends.backend_wxagg",
]

a = Analysis(
    [str(PROJECT / "main.py")],
    pathex=[str(PROJECT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="WaveHelm",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    icon=str(ICON),
    version=str(VERSION_FILE),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="WaveHelm",
)
