from __future__ import annotations

from types import SimpleNamespace

from src.boot import com_bootstrap


def test_init_com_tolerates_typed_bootstrap_failure(monkeypatch):
    class BrokenOle32:
        @property
        def CoInitializeEx(self):
            raise RuntimeError('ole fail')

    monkeypatch.setattr(com_bootstrap.ctypes, 'windll', SimpleNamespace(ole32=BrokenOle32()), raising=False)

    com_bootstrap.init_com()
