from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REQUIREMENTS_PATH = ROOT / "requirements.txt"
RUNTIME_ROOTS = [ROOT / "main.py", ROOT / "src"]
SKIP_PARTS = {"ui", "_bootstrap_runtime", "__pycache__"}
WX_UI_ROOT = ROOT / "src" / "ui_wx"

IMPORT_TO_REQUIREMENT = {
    "PIL": "Pillow",
    "comtypes": "comtypes",
    "cv2": "opencv-python",
    "matplotlib": "matplotlib",
    "tinytag": "tinytag",
    "numpy": "numpy",
    "pygame": "pygame",
    "scipy": "scipy",
    "soundfile": "soundfile",
    "win32api": "pywin32",
    "wx": "wxPython",
}

EXPECTED_REQUIREMENTS = {
    "wxPython",
    "pygame",
    "numpy",
    "scipy",
    "soundfile",
    "tinytag",
    "Pillow",
    "opencv-python",
    "matplotlib",
    "pywin32",
    "comtypes",
}


def _iter_python_files():
    for runtime_root in RUNTIME_ROOTS:
        if runtime_root.is_file():
            yield runtime_root
            continue
        for path in runtime_root.rglob("*.py"):
            if any(part in SKIP_PARTS for part in path.parts):
                continue
            yield path


def _declared_requirements() -> set[str]:
    names: set[str] = set()
    for raw_line in REQUIREMENTS_PATH.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        line = line.split(";", 1)[0].strip()
        for separator in ("==", ">=", "<=", "~=", "!=", ">", "<"):
            if separator in line:
                line = line.split(separator, 1)[0].strip()
                break
        names.add(line)
    return names


def _collect_dynamic_imported_roots(tree: ast.AST) -> set[str]:
    imported: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute):
            continue
        if func.attr != 'import_module' or not node.args:
            continue
        owner = func.value
        if not isinstance(owner, ast.Name) or owner.id != 'importlib':
            continue
        module_arg = node.args[0]
        if isinstance(module_arg, ast.Constant) and isinstance(module_arg.value, str):
            imported.add(module_arg.value.split('.', 1)[0])
    return imported


def _runtime_requirements_from_imports() -> set[str]:
    requirements: set[str] = set()
    for path in _iter_python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                imported.add(node.module.split(".", 1)[0])
        imported.update(_collect_dynamic_imported_roots(tree))
        for module_name in imported:
            requirement = IMPORT_TO_REQUIREMENT.get(module_name)
            if requirement:
                requirements.add(requirement)
    return requirements


def test_runtime_requirements_match_current_imported_stack():
    declared = _declared_requirements()
    imported = _runtime_requirements_from_imports()

    assert imported == EXPECTED_REQUIREMENTS
    assert declared == EXPECTED_REQUIREMENTS


def test_obsolete_requests_dependency_is_absent():
    declared = _declared_requirements()
    assert "requests" not in declared


def test_wx_ui_modules_keep_wx_imports_lazy_after_cutover():
    direct_imports: list[str] = []
    for path in WX_UI_ROOT.glob('*.py'):
        tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == 'wx' or alias.name.startswith('wx.'):
                        direct_imports.append(path.name)
            elif isinstance(node, ast.ImportFrom) and node.module and (node.module == 'wx' or node.module.startswith('wx.')):
                direct_imports.append(path.name)
    assert direct_imports == []
