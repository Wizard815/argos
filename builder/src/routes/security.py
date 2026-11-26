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
    monthly_tokens, daily_usd, monthly_usd, mcp_tools, mcp_tools_deny, note, apps?}}. Rules left out of a save that
    has no "apps" keep the key's app rules (the key's own form does not show them)."""
    if not _may_change():
        return err("log in to change keys", 401)
    d = body()
    rules = dict(d.get("rules") or {})
    if "apps" not in rules:
        old = next((k for k in sec().list_keys() if k["id"] == key_id), None)
        rules["apps"] = (old or {}).get("rules", {}).get("apps") or {}
    try:
        k = sec().set_rules(key_id, rules, d.get("name"))
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
    from .. import apps as apps_mod
    return ok(rows=gateway.usage_report(store, days), days=days,
              live={k["id"]: limiter().view(k["id"]) for k in keys}, keys={k["id"]: k["name"] for k in keys},
              apps=apps_mod.seen(store))


@bp.delete("/api/security/keys/<int:key_id>/apps")
def security_key_apps_forget(key_id):
    """Forget the apps seen through a key (?app=<name>: just that one); they are listed again when they next come."""
    if not _may_change():
        return err("log in to change this", 401)
    from .. import apps as apps_mod
    store = current_app.extensions["pb"].store
    app_name = request.args.get("app")
    apps_mod.forget(store, key_id, app_name)
    return ok(apps=apps_mod.seen(store, key_id))


@bp.put("/api/security/keys/<int:key_id>/apps/<path:app_name>")
def security_key_app_rules(key_id, app_name):
    """Body: {models, rpm, tpm, parallel, daily_tokens, monthly_tokens, daily_usd, monthly_usd, mcp_tools_deny,
    blocked, note}: one app's own rules under a key, on top of the key's."""
    if not _may_change():
        return err("log in to change keys", 401)
    k = next((x for x in sec().list_keys() if x["id"] == key_id), None)
    if not k:
        return err("no such key", 404)
    rules = dict(k["rules"])
    rules["apps"] = {**(rules.get("apps") or {}), app_name.strip()[:60]: body()}
    try:
        k = sec().set_rules(key_id, rules)
    except ValueError as exc:
        return err(str(exc))
    current_app.extensions["pb"].event("settings", f"API key {k['name']}: rules for the app {app_name} changed")
    return ok(key=k)


@bp.delete("/api/security/keys/<int:key_id>/apps/<path:app_name>/rules")
def security_key_app_rules_drop(key_id, app_name):
    if not _may_change():
        return err("log in to change keys", 401)
    k = next((x for x in sec().list_keys() if x["id"] == key_id), None)
    if not k:
        return err("no such key", 404)
    rules = dict(k["rules"])
    rules["apps"] = {a: r for a, r in (rules.get("apps") or {}).items() if a != app_name}
    return ok(key=sec().set_rules(key_id, rules))


def _setting(name, default):
    return current_app.extensions["pb"].store.get_settings().get(name) or default


@bp.get("/api/gateway")
def gateway_settings():
    if not _may_change():
        return err("log in to see this", 401)
    from .. import gateway
    from ..engine_proxy import answer_cache
    store = current_app.extensions["pb"].store
    from ..engine_proxy import health, convert_settings
    from .. import traces
    return ok(groups=_setting(gateway.GROUPS_KEY, {}), cache={**gateway.cache_settings(store), **answer_cache().view()},
              fallbacks=[gateway.public_fallback(r) for r in gateway.fallback_rules(store)],
              retry=gateway.retry_settings(store), cooling=health().view(), convert=convert_settings(store),
              traces={**traces.public(_setting(traces.SETTINGS_KEY, {})),
                      "stats": (current_app.extensions.get("traces").stats() if current_app.extensions.get("traces") else {})})


@bp.put("/api/gateway/convert")
def gateway_convert():
    """Body: {responses, messages}: "auto", "always" or "never" turn into chat completions."""
    if not _may_change():
        return err("log in to change this", 401)
    from ..engine_proxy import CONVERT_KEY
    d = body()
    for k in ("responses", "messages"):
        if d.get(k) not in (None, "auto", "always", "never"):
            return err(f"{k} is auto, always or never")
    store = current_app.extensions["pb"].store
    old = store.get_settings().get(CONVERT_KEY) or {}
    store.set_setting(CONVERT_KEY, {**old, **{k: d[k] for k in ("responses", "messages") if d.get(k)}})
    return gateway_settings()


@bp.put("/api/gateway/traces")
def gateway_traces():
    """Body: traces.clean's settings: where each model request is sent as an OpenTelemetry span."""
    if not _may_change():
        return err("log in to change this", 401)
    from .. import traces
    store = current_app.extensions["pb"].store
    try:
        v = traces.clean(body(), store.get_settings().get(traces.SETTINGS_KEY) or {})
    except ValueError as exc:
        return err(str(exc))
    store.set_setting(traces.SETTINGS_KEY, v)
    _TRACE_CACHE.clear()
    current_app.extensions["pb"].event("settings", "request traces " + (f"on: {v.get('endpoint')}" if v.get("enabled") else "off"))
    return gateway_settings()


@bp.post("/api/gateway/traces/test")
def gateway_traces_test():
    """Send one test span with the saved settings (or the ones in the body): "" ok, else what went wrong."""
    if not _may_change():
        return err("log in to change this", 401)
    from .. import traces
    store = current_app.extensions["pb"].store
    old = store.get_settings().get(traces.SETTINGS_KEY) or {}
    try:
        v = traces.clean(body() or old, old) if body() else old
    except ValueError as exc:
        return err(str(exc))
    problem = traces.test_send(v)
    return err(problem, 502) if problem else ok(sent=True)


_TRACE_CACHE: dict = {}


def trace_settings_getter(store):
    """Settings for the exporter, read at most every 2 s (it asks on every request)."""
    import time as _t
    from .. import traces

    def get():
        hit = _TRACE_CACHE.get("v")
        if hit and _t.time() - hit[0] < 2:
            return hit[1]
        from .. import features
        v = store.get_settings().get(traces.SETTINGS_KEY) or {}
        if not features.on(store, "traces"):
            v = {**v, "enabled": False}  # Settings -> General -> Features: traces off
        _TRACE_CACHE["v"] = (_t.time(), v)
        return v
    return get


@bp.put("/api/gateway/retry")
def gateway_retry():
    """Body: {retries, fails, window_s, cooldown_s}: try a failed request again, and rest a model or provider that
    keeps failing."""
    if not _may_change():
        return err("log in to change this", 401)
    from .. import gateway
    try:
        v = gateway.clean_retry({**gateway.RETRY_DEFAULTS, **body()})
    except ValueError as exc:
        return err(str(exc))
    current_app.extensions["pb"].store.set_setting(gateway.RETRY_KEY, v)
    return gateway_settings()


@bp.post("/api/gateway/cooling/reset")
def gateway_cooling_reset():
    """Body: {target?}: end a cooldown now (all of them without a target)."""
    if not _may_change():
        return err("log in to change this", 401)
    from ..engine_proxy import health
    health().reset(body().get("target") or None)
    return gateway_settings()


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


# ----- Settings -> Providers (providers.py) -------------------------------------------------------------------------
@bp.get("/api/providers")
def providers_list():
    if not _may_change():
        return err("log in to see this", 401)
    from .. import providers
    return ok(providers=[providers.public(p) for p in providers.listed(current_app.extensions["pb"].store)])


@bp.post("/api/providers")
def providers_save():
    """Add or change (``replace``: its old name) a provider. A blank api_key keeps the saved one."""
    if not _may_change():
        return err("log in to change this", 401)
    from .. import providers
    store, d = current_app.extensions["pb"].store, body()
    have = providers.listed(store)
    old = next((p for p in have if p["name"] == (d.get("replace") or d.get("name"))), None)
    try:
        p = providers.clean(d, old)
    except ValueError as exc:
        return err(str(exc))
    rest = [x for x in have if x is not old and x["name"] != p["name"]]
    store.set_setting(providers.KEY, rest + [p])
    providers.warm_prices([p])  # its model list says its prices: known before the first request
    current_app.extensions["pb"].event("settings", f"provider {p['name']} saved: {len(p['models'])} model(s) offered")
    return providers_list()


@bp.delete("/api/providers/<name>")
def providers_delete(name):
    if not _may_change():
        return err("log in to change this", 401)
    from .. import providers
    store = current_app.extensions["pb"].store
    store.set_setting(providers.KEY, [p for p in providers.listed(store) if p["name"] != name])
    return providers_list()


@bp.get("/api/providers/<name>/catalog")
def providers_catalog(name):
    """Every model the provider offers, to pick from (?fresh=1 asks again)."""
    if not _may_change():
        return err("log in to see this", 401)
    from .. import providers
    p = next((x for x in providers.listed(current_app.extensions["pb"].store) if x["name"] == name), None)
    if not p:
        return err("no such provider", 404)
    try:
        return ok(models=providers.catalog(p, fresh=bool(request.args.get("fresh"))), picked=p["models"])
    except ValueError as exc:
        return err(str(exc), 502)


@bp.get("/api/library/family-prefix")
def family_prefix_get():
    from ..model_groups import PREFIX_KEY
    return ok(on=bool(current_app.extensions["pb"].store.get_settings().get(PREFIX_KEY)))


@bp.put("/api/library/family-prefix")
def family_prefix_set():
    """Body: {on}: put each local model's family in front of its name in the list apps see (qwen/<name>)."""
    if not _may_change():
        return err("log in to change this", 401)
    from ..model_groups import PREFIX_KEY
    on = bool(body().get("on"))
    current_app.extensions["pb"].store.set_setting(PREFIX_KEY, on)
    current_app.extensions["pb"].event("settings", "apps' model list: family in front of names " + ("on" if on else "off"))
    return ok(on=on)
