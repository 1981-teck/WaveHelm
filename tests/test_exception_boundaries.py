from __future__ import annotations

import ast
import sys
from pathlib import Path
from types import ModuleType

import pytest

from src.video import mf_base
from src.video.component_base.definitions_comtypes import load_comtypes_symbols


class _BrokenModule(ModuleType):
    def __getattr__(self, name: str):
        if name == "boom":
            raise RuntimeError("unexpected resolver failure")
        raise AttributeError(name)


def _fake_comtypes(*, guid_error: Exception | None = None) -> ModuleType:
    module = ModuleType("comtypes")

    class IUnknown:
        pass

    class COMObject:
        pass

    def guid(value: str):
        if guid_error is not None:
            raise guid_error
        return value

    module.IUnknown = IUnknown
    module.COMObject = COMObject
    module.GUID = guid
    module.COMMETHOD = lambda *args, **kwargs: (args, kwargs)
    module.HRESULT = int
    return module


def test_comtypes_absence_degrades_to_explicit_stubs(monkeypatch):
    monkeypatch.setitem(sys.modules, "comtypes", None)

    symbols = load_comtypes_symbols("{00000000-0000-0000-0000-000000000000}")

    assert symbols[0] is None
    assert symbols[1] is False
    with pytest.raises(RuntimeError, match="comtypes non disponibile"):
        symbols[2]()


def test_comtypes_incomplete_api_is_not_hidden(monkeypatch):
    monkeypatch.setitem(sys.modules, "comtypes", ModuleType("comtypes"))

    with pytest.raises(ImportError):
        load_comtypes_symbols("{00000000-0000-0000-0000-000000000000}")


def test_comtypes_interface_construction_errors_are_not_hidden(monkeypatch):
    monkeypatch.setitem(
        sys.modules,
        "comtypes",
        _fake_comtypes(guid_error=ValueError("bad GUID construction")),
    )

    with pytest.raises(ValueError, match="bad GUID construction"):
        load_comtypes_symbols("{00000000-0000-0000-0000-000000000000}")


def test_mf_base_export_skips_only_missing_attributes():
    module = ModuleType("wavehelm_test_optional_exports")
    missing_name = "_wavehelm_test_missing_export"

    mf_base._export(module, [missing_name], source_label="test")

    assert missing_name not in vars(mf_base)


def test_mf_base_export_propagates_unexpected_resolution_errors():
    module = _BrokenModule("wavehelm_test_broken_exports")

    with pytest.raises(RuntimeError, match="unexpected resolver failure"):
        mf_base._export(module, ["boom"], source_label="test")


def test_only_documented_arbitrary_exception_boundaries_remain():
    src_root = Path(__file__).resolve().parents[1] / "src"
    broad_aliases: set[tuple[str, str]] = set()

    for path in src_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue
            value = node.value
            if not (
                isinstance(value, ast.Tuple)
                and len(value.elts) == 1
                and isinstance(value.elts[0], ast.Name)
                and value.elts[0].id == "Exception"
            ):
                continue
            for target in node.targets:
                if isinstance(target, ast.Name):
                    broad_aliases.add((path.relative_to(src_root).as_posix(), target.id))

    assert broad_aliases == {
        ("audio/audio_event_models.py", "FILTER_CONDITION_EXCEPTIONS"),
        ("video/component_adapter/com_thread_manager.py", "COM_TASK_EXECUTION_EXCEPTIONS"),
    }
