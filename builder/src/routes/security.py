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


# ----- what each key may do, and the gateway's settings (gateway.py) ----------------------------------------------
@bp.put("/api/security/keys/<int:key_id>")
def security_key_rules(key_id):
    """Body: {name?, rules: {models, mcp_mode, mcps, expires_at, blocked, rpm, tpm, parallel, daily_tokens,
    monthly_tokens, note}}."""
    if not _may_change():
        return err("log in to change keys", 401)
    d = body()
    try:
        k = sec().set_rules(key_id, d.get("rules") or {}, d.get("name"))
    except ValueError as exc:
        return err(str(exc))
    if not k:
        return err("no such key", 404)
    current_app.extensions["pb"].event("settings", f"API key {k['name']}: rules changed")
    return ok(key=k, **sec().status())


@bp.get("/api/security/usage")
def security_usage():
    """Tokens and requests per key, day and model, and each key's last minute."""
    if not _may_change():
        return err("log in to see this", 401)
    from .. import gateway
    from ..engine_proxy import limiter
    days = max(1, min(request.args.get("days", 30, type=int), 400))
    store = current_app.extensions["pb"].store
    keys = sec().list_keys()
    return ok(rows=gateway.usage_report(store, days), days=days,
              live={k["id"]: limiter().view(k["id"]) for k in keys}, keys={k["id"]: k["name"] for k in keys})


def _setting(name, default):
    return current_app.extensions["pb"].store.get_settings().get(name) or default


@bp.get("/api/gateway")
def gateway_settings():
    if not _may_change():
        return err("log in to see this", 401)
    from .. import gateway
    from ..engine_proxy import answer_cache
    store = current_app.extensions["pb"].store
    return ok(groups=_setting(gateway.GROUPS_KEY, {}), cache={**gateway.cache_settings(store), **answer_cache().view()},
              fallbacks=[gateway.public_fallback(r) for r in gateway.fallback_rules(store)])


@bp.put("/api/gateway/groups")
def gateway_groups():
    """Body: {groups: {name: [model patterns]}}: a key's model list can name a group as @name."""
    if not _may_change():
        return err("log in to change this", 401)
    from .. import gateway
    groups = body().get("groups") or {}
    if not isinstance(groups, dict):
        return err("groups is {name: [models]}")
    clean = {}
    for n, pats in groups.items():
        n = str(n).strip()
        if not n or not n.replace("-", "").replace("_", "").isalnum():
            return err(f"a group name is letters, digits, - and _: {n!r}")
        if isinstance(pats, str):
            pats = pats.replace("\n", ",").split(",")
        clean[n] = [str(p).strip() for p in pats or [] if str(p).strip() and not str(p).strip().startswith("@")]
    current_app.extensions["pb"].store.set_setting(gateway.GROUPS_KEY, clean)
    return gateway_settings()


@bp.put("/api/gateway/cache")
def gateway_cache():
    """Body: {enabled, ttl_s, max_entries, models}."""
    if not _may_change():
        return err("log in to change this", 401)
    from .. import gateway
    d = body()
    try:
        s = {"enabled": bool(d.get("enabled")), "ttl_s": max(1.0, float(d.get("ttl_s") or 3600)),
             "max_entries": max(1, int(d.get("max_entries") or 500)),
             "models": [m.strip() for m in (d.get("models") or []) if str(m).strip()]}
    except (TypeError, ValueError):
        return err("ttl_s and max_entries are numbers")
    current_app.extensions["pb"].store.set_setting(gateway.CACHE_KEY, s)
    if not s["enabled"]:
        from ..engine_proxy import answer_cache
        answer_cache().clear()
    return gateway_settings()


@bp.post("/api/gateway/cache/clear")
def gateway_cache_clear():
    if not _may_change():
        return err("log in to change this", 401)
    from ..engine_proxy import answer_cache
    answer_cache().clear()
    return gateway_settings()


@bp.post("/api/gateway/fallbacks")
def gateway_fallback_save():
    """Add or change (``replace``: its old name) a fallback rule. A blank api_key keeps the saved one."""
    if not _may_change():
        return err("log in to change this", 401)
    from .. import gateway
    store = current_app.extensions["pb"].store
    d = body()
    rules = gateway.fallback_rules(store)
    old = next((r for r in rules if r.get("name") == d.get("replace")), None)
    if old and not d.get("api_key"):
        d["api_key"] = old.get("api_key", "")
    try:
        r = gateway.clean_fallback(d)
    except ValueError as exc:
        return err(str(exc))
    rules = [x for x in rules if x is not old and x.get("name") != r["name"]] + [r]
    store.set_setting(gateway.FALLBACK_KEY, rules)
    current_app.extensions["pb"].event("settings", f"fallback {r['name']} saved: {r['match']} -> {r['url']}")
    return gateway_settings()


@bp.delete("/api/gateway/fallbacks/<path:name>")
def gateway_fallback_delete(name):
    if not _may_change():
        return err("log in to change this", 401)
    from .. import gateway
    store = current_app.extensions["pb"].store
    store.set_setting(gateway.FALLBACK_KEY, [r for r in gateway.fallback_rules(store) if r.get("name") != name])
    return gateway_settings()
