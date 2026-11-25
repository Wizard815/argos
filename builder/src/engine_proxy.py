"""One port for Argos: the builder answers its own routes and passes everything else to the engine.

The engine (llama-swap fork) used to serve its own web page on the same port as
the OpenAI API, which made it unclear which port to open. Now the builder owns
the public port. Its routes win; any path it does not own goes to the engine,
so ``/v1/*``, ``/upstream/*``, ``/comfyui/*``, ``/running``, ``/unload``,
``/metrics``, ``/api/models/*`` and the other engine endpoints keep their paths.

Three engine GET paths share a name with a builder route (``/api/events``,
``/api/hardware``, ``/api/profiles``). The builder keeps those. The engine's
versions stay reachable under ``/engine/<path>``. The engine's own page
(``/`` and ``/ui``) is not forwarded at all.
"""
from __future__ import annotations

import http.client
import json
import re
import socket
import time
import urllib.parse
import urllib.request

from flask import Blueprint, Response, abort, current_app, g, request

from . import gateway



def limiter(app=None) -> gateway.Limiter:
    """The key limits of this app (in memory: one Argos, one process)."""
    ext = (app or current_app).extensions
    if "gateway_limiter" not in ext:
        ext["gateway_limiter"] = gateway.Limiter()
    return ext["gateway_limiter"]


def answer_cache(app=None) -> gateway.AnswerCache:
    ext = (app or current_app).extensions
    if "gateway_cache" not in ext:
        ext["gateway_cache"] = gateway.AnswerCache()
    return ext["gateway_cache"]

METHODS = ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]

# hop-by-hop headers (RFC 7230 6.1) and framing headers are never copied between the two hops
HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailer", "transfer-encoding", "upgrade", "host", "content-length",
}

bp = Blueprint("engine_proxy", __name__)


def _blocked(engine_path: str) -> bool:
    """The engine's own web page is removed from the public port on purpose."""
    return engine_path == "/" or engine_path == "/ui" or engine_path.startswith("/ui/")


def _json(message: str, status: int) -> Response:
    return Response(json.dumps({"error": message}), status, mimetype="application/json")


# uploads whisper reads; the "file" part is converted to 16 kHz WAV when ffmpeg is installed
AUDIO_UPLOADS = ("/v1/audio/transcriptions", "/v1/audio/translations")


def _converted_audio(engine_path: str, headers: dict) -> "bytes | None":
    """The upload with its audio as WAV, or None to forward the request as it came."""
    if request.method != "POST" or engine_path not in AUDIO_UPLOADS or not request.mimetype.startswith("multipart/"):
        return None
    pb = current_app.extensions.get("pb")
    runners = getattr(pb, "runners", None)
    ffmpeg = runners.ffmpeg() if runners else ""
    if not ffmpeg:
        return None
    from . import audio_convert
    form = {k: request.form.getlist(k) for k in request.form}
    files = {k: [(f.filename or "", f.mimetype or "", f.read()) for f in request.files.getlist(k)] for k in request.files}
    try:
        done = audio_convert.convert_form(form, files, ffmpeg)
    except Exception as exc:  # noqa: BLE001 - say so, and let whisper try the original
        pb.event("runners", f"ffmpeg could not convert an upload: {exc}", level="warn")
        done = None
    if done is None:
        # the form was read to look at it, so the body has to be rebuilt either way
        done = audio_convert.rebuild(form, files)
    else:
        pb.event("runners", f"converted {done[2]} for {engine_path}")
    headers["Content-Type"] = done[1]
    return done[0]


def _model_of(body: bytes, parsed) -> str:
    if isinstance(parsed, dict):
        return str(parsed.get("model") or "")
    m = re.search(rb'name="model"\r?\n\r?\n([^\r\n]*)', body[:200000])  # a multipart upload (audio, images)
    return m.group(1).decode("utf-8", "replace").strip() if m else ""


def _api_error(message: str, status: int, code: str) -> Response:
    return Response(json.dumps({"error": {"message": message, "type": "invalid_request_error", "code": code}}),
                    status, mimetype="application/json")


def _open(url: str, method: str, target: str, body: bytes, headers: dict, wait: float = 0):
    """Send a request; returns (conn, response). ``wait`` > 0: give up (socket.timeout) when no answer starts in
    that many seconds."""
    parts = urllib.parse.urlsplit(url)
    https = parts.scheme == "https"
    conn_cls = http.client.HTTPSConnection if https else http.client.HTTPConnection
    host, port = parts.hostname, parts.port or (443 if https else 80)
    # a server outside (a cloud fallback) is reached through HTTPS_PROXY / HTTP_PROXY when they are set, as curl
    # and every other tool here would; the engine on 127.0.0.1 never is
    proxy = urllib.request.getproxies().get(parts.scheme) if not urllib.request.proxy_bypass(host or "") else None
    if proxy and host not in ("127.0.0.1", "localhost", "::1"):
        p = urllib.parse.urlsplit(proxy if "://" in proxy else "http://" + proxy)
        conn = conn_cls(p.hostname, p.port or 80, timeout=600)
        if https:
            conn.set_tunnel(host, port)
        else:
            target = f"{url.rstrip('/')}{target}"
    else:
        conn = conn_cls(host, port, timeout=600)
    try:
        conn.request(method, target, body=body or None, headers=headers)
        if wait and conn.sock:
            conn.sock.settimeout(wait)
        resp = conn.getresponse()
        if wait and conn.sock:
            conn.sock.settimeout(600)
    except Exception:
        conn.close()
        raise
    return conn, resp


def _to_fallback(rule: dict, engine_path: str, parsed: dict, headers: dict):
    """The same request to the rule's server, under its model name and with its key."""
    base = urllib.parse.urlsplit(rule["url"])
    path = engine_path
    if base.path.rstrip("/").endswith("/v1") and path.startswith("/v1/"):
        path = path[3:]
    target = base.path.rstrip("/") + path
    body = dict(parsed)
    if rule.get("model"):
        body["model"] = rule["model"]
    data = json.dumps(body).encode()
    h = {"Content-Type": "application/json", "Accept": headers.get("Accept", "*/*"), "Content-Length": str(len(data))}
    if rule.get("api_key"):
        h["Authorization"] = f"Bearer {rule['api_key']}"
    if engine_path.startswith("/v1/messages"):
        h["anthropic-version"] = headers.get("anthropic-version") or headers.get("Anthropic-Version") or "2023-06-01"
        if rule.get("api_key"):
            h["x-api-key"] = rule["api_key"]
    return _open(f"{base.scheme}://{base.netloc}", "POST", target, data, h)


def _forward(engine_path: str) -> Response:
    engine_url = current_app.config.get("ENGINE_URL") or ""
    if not engine_url:
        abort(404)  # builder-only run: unknown paths get the builder's own 404
    if _blocked(engine_path):
        return _json("not found", 404)
    pb = current_app.extensions.get("pb")
    store = pb.store if pb is not None else None
    key = getattr(g, "api_key", None)
    LIMITER, CACHE = limiter(), answer_cache()

    query = request.query_string.decode("latin-1")
    target = engine_path + (f"?{query}" if query else "")
    headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP_BY_HOP}
    headers["X-Forwarded-For"] = request.remote_addr or ""
    headers["X-Forwarded-Host"] = request.host
    if key:
        headers["X-Argos-Key"] = key["name"]
    body = _converted_audio(engine_path, headers)
    if body is None:
        body = request.get_data()
    parsed = None
    if body[:1] == b"{":
        try:
            parsed = json.loads(body)
        except ValueError:
            parsed = None
    model = _model_of(body, parsed) if request.method == "POST" else ""
    if engine_path.startswith("/upstream/"):  # the model is the path's first part: /upstream/<model>/...
        first, _, rest = engine_path[len("/upstream/"):].partition("/")
        from .model_groups import KNOWN
        from .providers import listed as _providers
        if first in KNOWN and rest and store is not None and first not in {p["name"] for p in _providers(store)}:
            # /upstream/qwen/<name>/...: the family prefix apps may use, taken off
            engine_path = "/upstream/" + rest
            target = engine_path + (f"?{query}" if query else "")
            first = rest.split("/", 1)[0]
        model = urllib.parse.unquote(first)
    elif not model and request.method == "GET" and engine_path.rstrip("/") not in ("/v1/models", "/models"):
        model = request.args.get("model", "")  # a GET routed by ?model= (voices, a model's props)

    # ----- a family prefix (qwen/<name>) and the cloud providers (providers.py, model_groups.py) -----
    from . import model_groups, providers
    provs = providers.listed(store) if store is not None else []
    pnames = {p["name"] for p in provs}
    if model and isinstance(parsed, dict):
        bare = model_groups.strip_prefix(model, pnames)
        if bare is not None:
            parsed["model"] = bare
            body = json.dumps(parsed).encode()
            model = bare
    cloud = providers.resolve(model, provs) if (model and isinstance(parsed, dict)) else None

    # ----- the key's rules (gateway.py) -----
    groups = (store.get_settings().get(gateway.GROUPS_KEY) or {}) if (store is not None and key) else {}
    slot = False
    if key:
        rules = key["rules"]
        if model and not gateway.model_allowed(model, rules, groups):
            return _api_error(f"this API key may not use the model {model}", 403, "model_not_allowed")
        if request.method == "POST" and model:
            day = time.strftime("%Y-%m-%d")
            why = LIMITER.acquire(key["id"], rules,
                                  gateway.tokens_used(store, key["id"], day) if rules.get("daily_tokens") else 0,
                                  gateway.tokens_used(store, key["id"], day[:8] + "01") if rules.get("monthly_tokens") else 0)
            if why:
                return _api_error(why, 429, "rate_limit_exceeded")
            slot = True

    def done(tokens: int = 0) -> None:
        if slot:
            LIMITER.release(key["id"], tokens)

    act: dict = {}

    def act_end() -> None:
        if act.pop("name", None) is not None:
            from .moe import ACTIVITY
            ACTIVITY.end(model)

    try:
        # ----- MCP tools run here (mcp_chat.py) -----
        if engine_path == "/v1/chat/completions" and isinstance(parsed, dict) and pb is not None:
            from . import mcp_chat
            if mcp_chat.wants_mcp(parsed):
                send = None
                if cloud:
                    def send(b, _p=cloud[0], _m=cloud[1]):
                        c2, r2 = _to_fallback({"url": _p["url"], "api_key": _p["api_key"], "model": _m}, engine_path, b, headers)
                        try:
                            raw = r2.read()
                        finally:
                            c2.close()
                        try:
                            return r2.status, json.loads(raw), raw
                        except ValueError:
                            return r2.status, {}, raw
                out, tokens = mcp_chat.run(pb, engine_url, parsed, headers, key, send=send)
                if key and store is not None:
                    gateway.record_usage(store, key["id"], model, *tokens)
                done(sum(tokens))
                slot = False
                return out

        # ----- answer cache -----
        cache_key, cs = "", {}
        if isinstance(parsed, dict) and request.method == "POST" and store is not None:
            cs = gateway.cache_settings(store)
            cache_key = CACHE.key_for(engine_path, parsed, cs)
            if cache_key:
                hit = CACHE.get(cache_key, cs["ttl_s"])
                if hit:
                    if key:
                        gateway.record_usage(store, key["id"], model, 0, 0)
                    done(0)
                    slot = False
                    return Response(hit[2], hit[0], mimetype=hit[1].split(";")[0], headers={"X-Argos-Cache": "hit"})

        # ----- the engine, or the cloud fallback -----
        if model and not cloud:  # when each model was last used: a new MoE setting waits for it to be quiet (moe.py)
            from .moe import ACTIVITY
            ACTIVITY.start(model)
            act["name"] = model
        rule = None
        if isinstance(parsed, dict) and model and store is not None and not cloud:
            rule = gateway.fallback_for(model, gateway.fallback_rules(store))
        if body:
            headers["Content-Length"] = str(len(body))
        conn = resp = None
        fell_back, why_fb = False, ""
        if cloud:
            try:
                conn, resp = _to_fallback({"url": cloud[0]["url"], "api_key": cloud[0]["api_key"], "model": cloud[1]},
                                          engine_path, parsed, headers)
            except OSError as exc:
                return _json(f"the provider {cloud[0]['name']} is unreachable: {exc}", 502)
        try:
            if resp is None:
                conn, resp = _open(engine_url, request.method, target, body, headers, (rule or {}).get("wait_s") or 0)
        except socket.timeout:
            why_fb = f"no answer in {rule['wait_s']:g}s"
        except OSError as exc:
            if not (rule and rule.get("on_down")):
                return _json(f"engine unreachable: {exc}", 502)
            why_fb = f"engine unreachable: {exc}"
        if resp is not None and rule and resp.status >= 400:
            # an error is small: read it to see whether it calls for the fallback
            err_body = resp.read()
            conn.close()
            if (resp.status >= 500 and rule.get("on_down")) or (rule.get("on_too_long") and gateway.too_long(resp.status, err_body)):
                why_fb = f"the local model answered {resp.status}"
            else:
                out_headers = [(k, v) for k, v in resp.getheaders() if k.lower() not in HOP_BY_HOP]
                return Response(err_body, status=resp.status, headers=out_headers)
            resp = None
        if resp is None:
            try:
                conn, resp = _to_fallback(rule, engine_path, parsed, headers)
            except OSError as exc:
                return _json(f"the local model failed ({why_fb}) and so did the fallback {rule['name']}: {exc}", 502)
            fell_back = True
            if pb is not None:
                pb.event("gateway", f"{model}: sent to the fallback {rule['name']} ({why_fb})", level="warn")

        out_headers = [(k, v) for k, v in resp.getheaders() if k.lower() not in HOP_BY_HOP]
        if fell_back:
            out_headers.append(("X-Argos-Fallback", rule["name"]))
        if cloud:
            out_headers.append(("X-Argos-Provider", cloud[0]["name"]))
        streamed = "text/event-stream" in (resp.getheader("Content-Type") or "")
        status, ctype = resp.status, resp.getheader("Content-Type") or "application/json"
        is_list = (request.method == "GET" and engine_path.rstrip("/") in ("/v1/models", "/models")
                   and not request.args.get("all") and status == 200)
        if is_list:
            # the list apps see: the cloud models added, a key's own models only, grouped by family
            data = resp.read()
            conn.close()
            try:
                listing = json.loads(data)
                if not isinstance(listing.get("data"), list):
                    raise ValueError("not a model list")
                rows = list(listing["data"]) + providers.model_entries(provs)
                if key and key["rules"].get("models"):
                    rows = [m for m in rows if gateway.model_allowed(str(m.get("id") or ""), key["rules"], groups)]
                prefix = bool(store.get_settings().get(model_groups.PREFIX_KEY)) if store is not None else False
                listing["data"] = model_groups.arrange(rows, pnames, prefix)
                data = json.dumps(listing).encode()
            except (ValueError, AttributeError):
                pass
            return Response(data, status=status, headers=[(k, v) for k, v in out_headers if k.lower() != "content-length"])
        held = slot
        slot = False  # the stream below releases it
        mine = dict(act)
        act.clear()  # and ends the model's activity

        def stream():
            tail, whole = bytearray(), bytearray()
            keep_whole = bool(cache_key) and not streamed
            tokens = (0, 0)
            try:
                while True:
                    # read1 returns as soon as bytes arrive, so server-sent events stay live
                    chunk = resp.read1(65536)
                    if not chunk:
                        break
                    if key or keep_whole:
                        tail += chunk
                        del tail[:-131072]
                        if keep_whole and len(whole) < 4 * 2 ** 20:
                            whole.extend(chunk)
                    yield chunk
                if key and status < 400:
                    tokens = gateway.usage_from(bytes(tail), streamed)
                    gateway.record_usage(store, key["id"], model, *tokens)
                if keep_whole and status == 200:
                    CACHE.put(cache_key, status, ctype, bytes(whole), cs["max_entries"])
            finally:
                conn.close()
                if held:
                    LIMITER.release(key["id"], sum(tokens))
                if mine:
                    from .moe import ACTIVITY
                    ACTIVITY.end(model)

        return Response(stream(), status=status, headers=out_headers)
    finally:
        if slot:
            done(0)
        act_end()


@bp.route("/engine/<path:subpath>", methods=METHODS)
def engine_direct(subpath: str):
    """The engine's own path, even where the builder has a route with the same name."""
    return _forward("/" + subpath)


@bp.route("/<path:subpath>", methods=METHODS)
def engine_fallback(subpath: str):
    return _forward("/" + subpath)
