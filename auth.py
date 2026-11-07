"""Dispatcher accounts: one login per team member.

Passwords are never stored. Only a salted PBKDF2 hash is kept, in
dispatchers.json, which must NOT be committed (it's in .gitignore).
Manage accounts with manage_users.py.

If dispatchers.json doesn't exist yet, there's one fallback account,
username "dispatcher", with the password from $env:DISPATCH_PASSWORD
(default "dispatch"), so the app still works on a fresh clone.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from pathlib import Path

ITERATIONS = 200_000           # slow on purpose: makes guessing expensive
SESSION_HOURS = 12
MAX_FAILURES = 5               # wrong passwords before a lockout
LOCKOUT_SECONDS = 60


def hash_password(password, salt=None):
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERATIONS)
    return salt.hex(), digest.hex()


def check_password(password, salt_hex, hash_hex):
    _, digest = hash_password(password, bytes.fromhex(salt_hex))
    return hmac.compare_digest(digest, hash_hex)


class Accounts:
    def __init__(self, path, fallback_password="dispatch"):
        self.path = Path(path)
        self.fallback_password = fallback_password
        self.users = {}
        if self.path.exists():
            with open(self.path) as f:
                self.users = json.load(f).get("users", {})

    @property
    def using_fallback(self):
        return not self.users

    def save(self):
        with open(self.path, "w") as f:
            json.dump({"users": self.users}, f, indent=2)

    def add(self, username, display_name, password):
        username = username.strip().lower()
        if not username.isidentifier():
            raise ValueError("Usernames can use letters, numbers and _ only")
        if len(password) < 8:
            raise ValueError("Passwords need at least 8 characters")
        salt, digest = hash_password(password)
        self.users[username] = {"name": display_name or username, "salt": salt, "hash": digest}
        self.save()

    def remove(self, username):
        if self.users.pop(username.strip().lower(), None) is None:
            raise ValueError(f"No user called {username}")
        self.save()

    def check(self, username, password):
        """Return the person's display name if the login is right, else None."""
        username = (username or "").strip().lower()
        if self.using_fallback:
            ok = username == "dispatcher" and hmac.compare_digest(
                password.encode(), self.fallback_password.encode())
            return "Dispatcher" if ok else None
        user = self.users.get(username)
        if user and check_password(password, user["salt"], user["hash"]):
            return user["name"]
        # Spend the same time on unknown usernames, so they can't be detected
        if not user:
            hash_password(password)
        return None


class Sessions:
    """Logged-in browsers: token -> (display name, expiry time)."""

    def __init__(self, hours=SESSION_HOURS, clock=time.time):
        self.seconds = hours * 3600
        self.clock = clock
        self._sessions = {}

    def start(self, name):
        token = secrets.token_urlsafe(24)
        self._sessions[token] = (name, self.clock() + self.seconds)
        return token

    def user(self, token):
        entry = self._sessions.get(token)
        if entry is None:
            return None
        name, expires = entry
        if self.clock() > expires:
            del self._sessions[token]
            return None
        return name

    def end(self, token):
        self._sessions.pop(token, None)


class LoginLimiter:
    """After MAX_FAILURES wrong passwords from one address, wait LOCKOUT_SECONDS."""

    def __init__(self, clock=time.time):
        self.clock = clock
        self._failures = {}      # address -> (count, locked_until)

    def locked_for(self, key):
        count, until = self._failures.get(key, (0, 0))
        return max(0, int(until - self.clock()))

    def failed(self, key):
        count, _ = self._failures.get(key, (0, 0))
        count += 1
        until = self.clock() + LOCKOUT_SECONDS if count >= MAX_FAILURES else 0
        self._failures[key] = (0 if until else count, until)

    def succeeded(self, key):
        self._failures.pop(key, None)
