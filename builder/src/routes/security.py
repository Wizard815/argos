"""The login page and Settings -> Security: the web page password and API keys (see security.py)."""
from __future__ import annotations

import html
import time
import urllib.parse

from flask import Blueprint, current_app, redirect, request, session

from ..web import body, err, ok

bp = Blueprint("security", __name__)


def sec():
    return current_app.extensions["security"]


def logged_in() -> bool:
    return bool(session.get("auth")) and session.get("stamp") == sec().password_stamp()


def _local() -> bool:
    """Inside the container, not a reverse proxy on the same host (that one says whom it forwards for)."""
    return (request.remote_addr or "") in ("127.0.0.1", "::1") and not (
        request.headers.get("X-Forwarded-For") or request.headers.get("X-Real-IP") or request.headers.get("Forwarded"))


LOGIN_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Argos · log in</title>
<style>
:root { --bg:#16181d; --card:#1e2128; --fg:#d7dae0; --muted:#8b93a1; --line:#2c313a; --accent:#e06c75; }
@media (prefers-color-scheme: light) { :root { --bg:#f4f5f7; --card:#fff; --fg:#22252b; --muted:#5f6672; --line:#dde1e6; } }
body { margin:0; min-height:100vh; display:grid; place-items:center; background:var(--bg); color:var(--fg);
  font:14px/1.5 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
form { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:28px; width:min(340px, 90vw); }
h1 { margin:0 0 4px; font-size:20px; color:var(--accent); } p { margin:0 0 18px; color:var(--muted); }
input { width:100%; box-sizing:border-box; padding:10px; border-radius:6px; border:1px solid var(--line); background:var(--bg);
  color:var(--fg); font:inherit; } button { margin-top:12px; width:100%; padding:10px; border:0; border-radius:6px;
  background:var(--accent); color:#fff; font:inherit; font-weight:700; cursor:pointer; }
.err { color:var(--accent); margin:10px 0 0; }
</style></head><body><form method="post" action="/login">
<h1>Argos</h1><p>Enter the password set in Settings &rarr; Security.</p>
<input type="hidden" name="next" value="{next}">
<input type="password" name="password" autofocus autocomplete="current-password" placeholder="password" required>
<button type="submit">Log in</button>{error}
</form><script>
// the page you asked for (#schedule) never reaches the server: keep it for after the login
var n = document.querySelector("[name=next]");
if (location.hash && n.value.indexOf("#") < 0) n.value += location.hash;
</script></body></html>"""


def _safe_next(target: str) -> str:
    """Only a path on this site: never send someone on to another one after logging in."""
    target = target or "/"
    parts = urllib.parse.urlsplit(target)
    return target if not parts.scheme and not parts.netloc and target.startswith("/") and not target.startswith("//") else "/"


@bp.route("/login", methods=["GET", "POST"])
def login():
    nxt = _safe_next(request.values.get("next") or "/")
    if not sec().password_set():
        return redirect(nxt)
    error = ""
    if request.method == "POST":
        if sec().check_password(request.form.get("password") or "", request.remote_addr or ""):
            session.clear()
            session.permanent = True
            session["auth"], session["stamp"], session["at"] = True, sec().password_stamp(), time.time()
            return redirect(nxt)
        error = '<p class="err">That is not the password.</p>'
    page = LOGIN_PAGE.replace("{next}", html.escape(nxt, quote=True)).replace("{error}", error)
    return current_app.response_class(page, status=401 if error else 200, mimetype="text/html")


@bp.route("/logout", methods=["GET", "POST"])
def logout():
    session.clear()
    return redirect("/login")


@bp.get("/api/auth/status")
def auth_status():
    return ok(password_set=sec().password_set(), default_password=sec().default_password(),
              logged_in=logged_in() or not sec().password_set())


# ----- Settings -> Security: only a logged-in browser (or one with no password set yet), never an API key ---------
def _may_change() -> bool:
    return not sec().password_set() or logged_in() or _local()


@bp.get("/api/security")
def security_status():
    if not _may_change():
        return err("log in to see this", 401)
    return ok(**sec().status())


@bp.put("/api/security/password")
def security_password():
    if not _may_change():
        return err("log in to change this", 401)
    d = body()
    try:
        sec().set_password(str(d.get("new") or ""), str(d.get("current") or ""))
    except PermissionError as exc:
        return err(str(exc), 403)
    except ValueError as exc:
        return err(str(exc))
    if sec().password_set():  # this browser stays logged in with the new password
        session.permanent = True
        session["auth"], session["stamp"], session["at"] = True, sec().password_stamp(), time.time()
    else:
        session.clear()
    current_app.extensions["pb"].event("settings", "web page password " + ("set" if sec().password_set() else "removed"))
    return ok(**sec().status())


@bp.post("/api/security/keys")
def security_key_new():
    if not _may_change():
        return err("log in to make keys", 401)
    made = sec().create_key(str(body().get("name") or ""))
    current_app.extensions["pb"].event("settings", f"API key made: {made['name']} ({made['prefix']}…)")
    return ok(key=made, **sec().status())


@bp.delete("/api/security/keys/<int:key_id>")
def security_key_delete(key_id):
    if not _may_change():
        return err("log in to revoke keys", 401)
    if not sec().delete_key(key_id):
        return err("no such key", 404)
    current_app.extensions["pb"].event("settings", f"API key {key_id} revoked")
    return ok(**sec().status())


@bp.put("/api/security/require-key")
def security_require_key():
    if not _may_change():
        return err("log in to change this", 401)
    on = bool(body().get("on"))
    if on and not sec().list_keys():
        return err("make a key first, or every app is locked out")
    sec().set_require_key(on)
    current_app.extensions["pb"].event("settings", "model API key " + ("required" if on else "no longer required"))
    return ok(**sec().status())
