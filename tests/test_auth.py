"""Personal dispatcher accounts, sessions and the login lockout.

Run from the project root with:  python -m pytest tests/test_auth.py -v
"""
import json

import pytest

import auth
from auth import Accounts, LoginLimiter, Sessions


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


# ---------- accounts ----------

def test_passwords_are_never_stored(tmp_path):
    path = tmp_path / "dispatchers.json"
    Accounts(path).add("aashi", "Aashi", "correct horse battery")
    text = path.read_text()
    assert "correct horse battery" not in text
    saved = json.loads(text)["users"]["aashi"]
    assert set(saved) == {"name", "salt", "hash"}


def test_login_with_personal_account(tmp_path):
    path = tmp_path / "dispatchers.json"
    Accounts(path).add("Aashi", "Aashi", "correct horse battery")
    accounts = Accounts(path)                      # reload from disk
    assert accounts.check("aashi", "correct horse battery") == "Aashi"
    assert accounts.check("AASHI ", "correct horse battery") == "Aashi"   # forgiving username
    assert accounts.check("aashi", "wrong password") is None
    assert accounts.check("nobody", "correct horse battery") is None


def test_same_password_gives_different_hashes(tmp_path):
    accounts = Accounts(tmp_path / "d.json")
    accounts.add("a", "A", "samepassword")
    accounts.add("b", "B", "samepassword")
    assert accounts.users["a"]["hash"] != accounts.users["b"]["hash"]   # salted


def test_short_passwords_and_bad_usernames_rejected(tmp_path):
    accounts = Accounts(tmp_path / "d.json")
    with pytest.raises(ValueError):
        accounts.add("aashi", "Aashi", "short")
    with pytest.raises(ValueError):
        accounts.add("bad name!", "X", "long enough password")


def test_remove(tmp_path):
    accounts = Accounts(tmp_path / "d.json")
    accounts.add("aashi", "Aashi", "long enough password")
    accounts.remove("aashi")
    assert accounts.check("aashi", "long enough password") is None


def test_fallback_account_until_someone_signs_up(tmp_path):
    accounts = Accounts(tmp_path / "none.json", fallback_password="team-secret")
    assert accounts.using_fallback
    assert accounts.check("dispatcher", "team-secret") == "Dispatcher"
    assert accounts.check("dispatcher", "dispatch") is None
    accounts.add("aashi", "Aashi", "long enough password")
    assert accounts.check("dispatcher", "team-secret") is None   # fallback switches off


# ---------- sessions and lockout ----------

def test_sessions_expire():
    clock = FakeClock()
    sessions = Sessions(hours=12, clock=clock)
    token = sessions.start("Aashi")
    assert sessions.user(token) == "Aashi"
    clock.t += 12 * 3600 + 1
    assert sessions.user(token) is None


def test_logout_ends_session():
    sessions = Sessions()
    token = sessions.start("Aashi")
    sessions.end(token)
    assert sessions.user(token) is None


def test_lockout_after_repeated_failures():
    clock = FakeClock()
    limiter = LoginLimiter(clock=clock)
    for _ in range(auth.MAX_FAILURES - 1):
        limiter.failed("1.2.3.4")
    assert limiter.locked_for("1.2.3.4") == 0
    limiter.failed("1.2.3.4")
    assert limiter.locked_for("1.2.3.4") > 0
    assert limiter.locked_for("5.6.7.8") == 0      # other people unaffected
    clock.t += auth.LOCKOUT_SECONDS + 1
    assert limiter.locked_for("1.2.3.4") == 0


# ---------- through the server ----------

httpx = pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

import server  # noqa: E402


@pytest.fixture
def team(tmp_path, monkeypatch):
    accounts = Accounts(tmp_path / "dispatchers.json")
    accounts.add("aashi", "Aashi", "correct horse battery")
    monkeypatch.setattr(server, "accounts", accounts)
    monkeypatch.setattr(server, "limiter", LoginLimiter())
    return TestClient(server.app)


def login(client, username="aashi", password="correct horse battery"):
    return client.post("/api/login", json={"username": username, "password": password})


def test_server_personal_login_and_me(team):
    assert login(team).status_code == 200
    assert team.get("/api/me").json()["name"] == "Aashi"


def test_server_rejects_shared_fallback_once_accounts_exist(team):
    assert login(team, "dispatcher", server.DISPATCH_PASSWORD).status_code == 401


def test_server_lockout(team):
    for _ in range(auth.MAX_FAILURES):
        assert login(team, password="guess").status_code == 401
    res = login(team)                                   # even the right password waits
    assert res.status_code == 429
    assert "Try again" in res.json()["detail"]


def test_actions_are_signed_with_the_users_name(team):
    login(team)
    team.post("/api/reset")
    team.post("/api/edges/12/close")
    team.post("/api/trucks/V3/breakdown")
    messages = [e["message"] for e in team.get("/api/state").json()["events"]]
    assert any("Road closed" in m and "by Aashi" in m for m in messages)
    assert any("broke down" in m and "by Aashi" in m for m in messages)
