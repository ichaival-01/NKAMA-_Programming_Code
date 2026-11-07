"""Shared test setup, applied to every test automatically.

Tests must never depend on what's on a particular laptop. If a teammate has
created personal accounts (dispatchers.json), the server switches off the
shared "dispatcher" login, which the server tests use. So for every test,
the server gets a fresh, empty accounts list (the shared login works again)
and a fresh login limiter (no lockout left over from an earlier test).
Your real dispatchers.json is never read or changed by the tests.
"""
import sys

import pytest


@pytest.fixture(autouse=True)
def isolated_logins(tmp_path, monkeypatch):
    server = sys.modules.get("server")
    if server is not None:
        from auth import Accounts, LoginLimiter, Sessions
        monkeypatch.setattr(server, "accounts",
                            Accounts(tmp_path / "no-accounts.json", server.DISPATCH_PASSWORD))
        monkeypatch.setattr(server, "limiter", LoginLimiter())
        monkeypatch.setattr(server, "sessions", Sessions())
    yield
