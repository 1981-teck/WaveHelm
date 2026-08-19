from __future__ import annotations

import sqlite3

import pytest

from src.model.component_database.settings_manager import SettingsManager


class DummyDbCore:
    def __init__(self, *, row=None, exc=None):
        self.row = row
        self.exc = exc
        self.calls = []

    def _execute_query(self, query, params, fetch_one=False):
        self.calls.append((query, params, fetch_one))
        if self.exc is not None:
            raise self.exc
        return self.row


def test_get_and_set_app_setting_use_db_core_queries():
    db = DummyDbCore(row={'value': 'dark'})
    manager = SettingsManager(db)

    assert manager.get_app_setting('theme', 'light') == 'dark'
    manager.set_app_setting('theme', 'light')

    assert db.calls[0][1] == ('theme',)
    assert db.calls[0][2] is True
    assert db.calls[1][1] == ('theme', 'light')


def test_get_returns_default_and_set_is_best_effort_on_sqlite_error():
    db = DummyDbCore(exc=sqlite3.OperationalError('boom'))
    manager = SettingsManager(db)

    assert manager.get_app_setting('theme', 'light') == 'light'
    manager.set_app_setting('theme', 'dark')


def test_programming_errors_are_not_swallowed():
    manager = SettingsManager(object())

    with pytest.raises(AttributeError):
        manager.get_app_setting('theme')
