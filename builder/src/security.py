"""Who may use Argos: a password for the web page, API keys for apps.

Both are off until set (Settings -> Security), and both are checked here, in the builder, which owns the one public
port: the engine listens on 127.0.0.1 inside the container, so nothing reaches it except through this.

- The password guards the web page and the builder's own /api. A browser logs in once (/login) and gets a signed
  cookie for ``SESSION_DAYS``. Changing the password signs every browser out.
- API keys are for apps (Hermes, Codex, Odysseus, a dashboard): ``Authorization: Bearer <key>`` or ``x-api-key``.
  With "require a key" on, the model API (/v1/... and everything else passed to the engine) needs one, or a
  logged-in browser. A key also opens the builder's /api, but not Settings -> Security itself.
- Requests from inside the container (127.0.0.1: the engine calling back, ``docker exec ... curl``) pass.

The password is kept as a scrypt hash and each key as its SHA-256; a key is shown once, when it is made. A
forgotten password is cleared on the server: ``docker compose exec argos argos-password clear``.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from typing import Any, Optional

STATE_KEY = "_security"
DEFAULT_PASSWORD = "admin"
SESSION_DAYS = 30
KEY_PREFIX = "argos-"
# the paths a browser can reach before it has logged in
OPEN_PATHS = {"/health", "/login", "/logout", "/favicon.ico", "/api/auth/status"}
# the model API among the engine's paths (what apps call): it needs a key when keys are required. Every other path
# passed to the engine runs the engine itself (the pool, unloading, logs, metrics): it needs a login (or a key)
# once a password is set.
MODEL_API_PREFIXES = ("/v1/", "/upstream/", "/sdapi/", "/audioapi/", "/comfyui", "/completion", "/infill",
                      "/rerank", "/reranking", "/props", "/models")


def _hash_password(password: str, salt: Optional[bytes] = None) -> str:
    salt = salt or os.urandom(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return f"scrypt${salt.hex()}${dk.hex()}"


def _check_password(password: str, stored: str) -> bool:
    try:
        kind, salt, want = stored.split("$")
    except ValueError:
        return False
    if kind != "scrypt":
        return False
    got = _hash_password(password, bytes.fromhex(salt)).split("$")[2]
    return hmac.compare_digest(got, want)


def _key_hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


class Security:
    def __init__(self, store):
        self.store = store
        self._lock = threading.Lock()
        self._used: dict[int, float] = {}   # key id -> when its last use was written (once a minute at most)
        self._fails: dict[str, list[float]] = {}  # address -> recent failed logins
        self._cache: tuple[float, dict] = (0.0, {})

    # ----- state ------------------------------------------------------------------------------------------
    def state(self) -> dict:
        """Read from the database itself, at most 5 s old: ``argos-password clear`` runs in another process, and the
        app's settings cache would never see it."""
        if time.time() - self._cache[0] < 5:
            return self._cache[1]
        import json
        rows = self.store._q("SELECT value FROM settings WHERE key=?", (STATE_KEY,))
        s = dict(json.loads(rows[0]["value"]) if rows else {})
        if not s.get("session_secret"):  # signs the login cookies; made once
            s["session_secret"] = secrets.token_hex(32)
            self.store.set_setting(STATE_KEY, s)
        self._cache = (time.time(), s)
        return s

    def _save(self, **changes: Any) -> dict:
        s = {**self.state(), **changes}
        self.store.set_setting(STATE_KEY, s)
        self._cache = (time.time(), s)
        return s

    def password_set(self) -> bool:
        return bool(self.state().get("password_hash"))

    def require_key(self) -> bool:
        return bool(self.state().get("require_key"))

    def session_secret(self) -> str:
        return self.state()["session_secret"]

    def password_stamp(self) -> str:
        """Changes whenever the password does: a cookie from before a change no longer counts."""
        return hashlib.sha256((self.state().get("password_hash") or "").encode()).hexdigest()[:16]

    # ----- the password -------------------------------------------------------------------------------------
    def first_start(self, env_value) -> str:
        """The password a new install starts with, once, from ARGOS_UI_PASSWORD: not set at all is "admin", set
        but empty ("none" in setup.sh) is no login, anything else is that password. After the first start only
        Settings -> Security changes it. Returns what was done, for the log ("" when it was decided before)."""
        if self.state().get("initialized"):
            return ""
        pw = DEFAULT_PASSWORD if env_value is None else str(env_value)
        if pw.strip().lower() == "none":
            pw = ""
        self._save(initialized=True, password_hash=_hash_password(pw) if pw else "", default_password=pw == DEFAULT_PASSWORD)
        if not pw:
            return "no web page password (ARGOS_UI_PASSWORD is empty): Argos opens without logging in"
        if pw == DEFAULT_PASSWORD:
            return 'the web page password is the default, "admin": change it in Settings -> Security'
        return "the web page password is the one ARGOS_UI_PASSWORD gave"

    def default_password(self) -> bool:
        """Still the "admin" a new install started with: the page warns until it is changed."""
        return self.password_set() and bool(self.state().get("default_password"))

    def set_password(self, new: str, current: str = "") -> None:
        have = self.state().get("password_hash")
        if have and not _check_password(current, have):
            raise PermissionError("the current password is wrong")
        if new and len(new) < 8:
            raise ValueError("use at least 8 characters")
        self._save(password_hash=_hash_password(new) if new else "", default_password=False)

    def check_password(self, password: str, addr: str = "") -> bool:
        """With a pause and a limit (10 a minute from one address) against guessing."""
        now = time.time()
        recent = [t for t in self._fails.get(addr, []) if now - t < 60]
        if len(recent) >= 10:
            return False
        ok = _check_password(password, self.state().get("password_hash") or "")
        if not ok:
            self._fails[addr] = recent + [now]
            time.sleep(0.5)
        return ok

    def clear_password(self) -> None:
        self._save(password_hash="", default_password=False, initialized=True)

    # ----- API keys --------------------------------------------------------------------------------------------
    def create_key(self, name: str) -> dict:
        name = (name or "").strip()[:60] or "key"
        key = KEY_PREFIX + secrets.token_urlsafe(32)
        self.store._x("INSERT INTO api_keys (name, prefix, hash, created_at) VALUES (?, ?, ?, ?)",
                      (name, key[:12], _key_hash(key), time.time()))
        row = self.store._q("SELECT id, name, prefix, created_at, last_used_at FROM api_keys WHERE hash=?", (_key_hash(key),))[0]
        return {**row, "key": key}

    def list_keys(self) -> list[dict]:
        from .gateway import load_rules
        rows = self.store._q("SELECT id, name, prefix, created_at, last_used_at, rules FROM api_keys ORDER BY id")
        return [{**r, "rules": load_rules(r.get("rules"))} for r in rows]

    def set_rules(self, key_id: int, rules: dict, name: Optional[str] = None) -> Optional[dict]:
        """What a key may do (gateway.clean_rules); raises ValueError on a bad value."""
        from .gateway import clean_rules
        if not self.store._q("SELECT 1 AS x FROM api_keys WHERE id=?", (key_id,)):
            return None
        self.store._x("UPDATE api_keys SET rules=? WHERE id=?", (json.dumps(clean_rules(rules)), key_id))
        if name is not None and name.strip():
            self.store._x("UPDATE api_keys SET name=? WHERE id=?", (name.strip()[:60], key_id))
        return next((k for k in self.list_keys() if k["id"] == key_id), None)

    def delete_key(self, key_id: int) -> bool:
        if not self.store._q("SELECT 1 AS x FROM api_keys WHERE id=?", (key_id,)):
            return False
        self.store._x("DELETE FROM api_keys WHERE id=?", (key_id,))
        self.store._x("DELETE FROM key_apps WHERE key_id=?", (key_id,))
        return True

    def check_key(self, key: str) -> Optional[dict]:
        if not key:
            return None
        from .gateway import load_rules, usable
        rows = self.store._q("SELECT id, name, rules FROM api_keys WHERE hash=?", (_key_hash(key),))
        if not rows:
            return None
        row = {**rows[0], "rules": load_rules(rows[0].get("rules"))}
        if usable(row["rules"]):  # blocked or expired: refused like a wrong key
            return None
        now = time.time()
        with self._lock:
            if now - self._used.get(row["id"], 0) > 60:
                self._used[row["id"]] = now
                self.store._x("UPDATE api_keys SET last_used_at=? WHERE id=?", (now, row["id"]))
        return row

    def key_problem(self, key: str) -> str:
        """Why a real key is refused (blocked, expired), or "" for a key that is fine or unknown."""
        from .gateway import load_rules, usable
        rows = self.store._q("SELECT rules FROM api_keys WHERE hash=?", (_key_hash(key),)) if key else []
        return usable(load_rules(rows[0].get("rules"))) if rows else ""

    def set_require_key(self, on: bool) -> None:
        self._save(require_key=bool(on))

    def status(self) -> dict:
        return {"password_set": self.password_set(), "default_password": self.default_password(),
                "require_key": self.require_key(), "keys": self.list_keys(), "session_days": SESSION_DAYS}


def key_from(headers) -> str:
    """An API key from Authorization: Bearer, x-api-key, or Basic auth's password (what OpenAI clients send)."""
    auth = headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    if auth.lower().startswith("basic "):
        import base64
        try:
            return base64.b64decode(auth[6:]).decode("utf-8", "replace").partition(":")[2]
        except ValueError:
            return ""
    return headers.get("x-api-key", "").strip()


def main(argv: list[str]) -> int:
    """argos-password clear: remove the web page password (a forgotten one). status: say what is set."""
    from .db import Store
    db = os.environ.get("ARGOS_DATABASE_URL") or os.environ.get("ARGOS_BUILDER_DB") or "/data/builder.db"
    sec = Security(Store(db))
    cmd = argv[0] if argv else "status"
    if cmd == "clear":
        sec.clear_password()
        print("The web page password is removed: Argos opens without logging in. Set a new one in Settings -> Security.")
    elif cmd == "status":
        st = sec.status()
        print(f"password: {'set' if st['password_set'] else 'none'}; require a key for the model API: "
              f"{'yes' if st['require_key'] else 'no'}; keys: {len(st['keys'])}")
    else:
        print("usage: argos-password clear | status")
        return 2
    return 0


if __name__ == "__main__":
    import sys
    raise SystemExit(main(sys.argv[1:]))
