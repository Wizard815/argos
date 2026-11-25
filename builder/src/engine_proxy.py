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
import threading
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


def health(app=None) -> gateway.Health:
    """Failures and cooldowns of local models and providers (retries and cooldown, Settings -> Gateway)."""
    ext = (app or current_app).extensions
    if "gateway_health" not in ext:
        ext["gateway_health"] = gateway.Health()
    return ext["gateway_health"]


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


class ClientGone(OSError):
    """The app that sent the request hung up before the answer started."""


def _client_gone():
    """A check for whether the app has hung up (waitress, with channel_request_lookahead), or None."""
    try:
        return request.environ.get("waitress.client_disconnected")
    except RuntimeError:  # outside a request
        return None


def _open(url: str, method: str, target: str, body: bytes, headers: dict, wait: float = 0, gone=None):
    """Send a request; returns (conn, response). ``wait`` > 0: give up (socket.timeout) when no answer starts in
    that many seconds. ``gone``: a check for the app having hung up; while no answer has started it is asked
    every quarter second, and the request is dropped (ClientGone) when it says so. The server sees the connection
    close and drops it too: a prompt held in the engine's line, or read by the model, for nobody."""
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
    done, hung_up = threading.Event(), threading.Event()
    try:
        conn.request(method, target, body=body or None, headers=headers)
        if wait and conn.sock:
            conn.sock.settimeout(wait)
        if gone is not None and conn.sock is not None:
            sock = conn.sock

            def watch():
                while not done.wait(0.25):
                    try:
                        if gone():
                            hung_up.set()
                            sock.shutdown(socket.SHUT_RDWR)
                            return
                    except Exception:
                        return
            threading.Thread(target=watch, daemon=True, name="client-gone").start()
        try:
            resp = conn.getresponse()
        finally:
            done.set()
        if hung_up.is_set():
            raise ClientGone("the app hung up")
        if wait and conn.sock:
            conn.sock.settimeout(600)
    except Exception as exc:
        conn.close()
        if hung_up.is_set() and not isinstance(exc, ClientGone):
            raise ClientGone("the app hung up") from exc
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


def _record(store, key: dict, model: str, app: str, how: str, tokens_in: int, tokens_out: int,
            cost_usd: float = 0.0) -> None:
    """A key's usage (and what it cost, for a cloud model), and the app seen through it."""
    from . import apps as apps_mod
    gateway.record_usage(store, key["id"], model, tokens_in, tokens_out, cost_usd=cost_usd)
    try:
        apps_mod.record(store, key["id"], app, how, model, tokens_in, tokens_out, cost_usd=cost_usd)
    except Exception:  # noqa: BLE001 - the answer matters more than the list of apps
        pass


def _exporter():
    return current_app.extensions.get("traces")


def _send_trace(exp, info: dict, start: float, end: float, status: int, tokens=(None, None), cost=None, error: str = "",
                body: bytes | None = None, answer: str | None = None) -> None:
    """One span for a model request (traces.py; Settings -> Gateway -> Request traces). Never raises."""
    if exp is None or not info:
        return
    try:
        from . import traces
        exp.record(traces.span(name=f"{info['op']} {info['model']}", start=start, end=end, model=info["model"],
                               path=info["path"], status=status, key=info.get("key") or "", app=info.get("app") or "",
                               provider=info.get("provider") or "", input_tokens=tokens[0], output_tokens=tokens[1],
                               cost_usd=cost, stream=bool(info.get("stream")), error=error,
                               fallback=info.get("fallback") or "", request_body=body, response_text=answer))
    except Exception:  # noqa: BLE001 - a trace never breaks a request
        pass


def _answer_text(tail: bytes, streamed: bool) -> str:
    """The answer's text for a trace: the message (or the streamed deltas) when it can be read, else the raw tail."""
    text = tail.decode("utf-8", "replace")
    if not streamed:
        try:
            j = json.loads(text)
            c = (j.get("choices") or [{}])[0]
            return (c.get("message") or {}).get("content") or c.get("text") or text
        except (ValueError, AttributeError, IndexError, TypeError):
            return text
    parts = []
    for line in text.splitlines():
        if line.startswith("data:") and "[DONE]" not in line:
            try:
                d = (json.loads(line[5:]).get("choices") or [{}])[0].get("delta") or {}
                parts.append(d.get("content") or "")
            except (ValueError, AttributeError, IndexError, TypeError):
                continue
    return "".join(parts) or text


CONVERT_KEY = "_convert"
CONVERTED = {"/v1/responses": "responses", "/responses": "responses", "/v1/messages": "messages"}


def convert_settings(store) -> dict:
    """When /v1/responses and /v1/messages are turned into chat completions (api_translate.py): "auto" (a cloud
    provider's model, or a local model that answers it does not know the path), "always", or "never"."""
    v = (store.get_settings().get(CONVERT_KEY) if store is not None else None) or {}
    return {k: (v.get(k) if v.get(k) in ("auto", "always", "never") else "auto") for k in ("responses", "messages")}


def _converted(engine_path: str, kind: str, parsed: dict, why: str) -> Response:
    """The request as a chat completion, and its answer turned back into the format the app asked in."""
    from . import api_translate as tr
    chat = tr.responses_to_chat(parsed) if kind == "responses" else tr.messages_to_chat(parsed)
    resp = _forward_inner("/v1/chat/completions", json.dumps(chat).encode())
    hdrs = [(k, v) for k, v in resp.headers.items() if k.lower() not in ("content-length", "content-type")]
    hdrs.append(("X-Argos-Converted", f"{kind} -> chat ({why})"))
    if resp.is_streamed and resp.status_code < 400 and "event-stream" in (resp.content_type or ""):
        conv = tr.ChatToResponsesStream(parsed) if kind == "responses" else tr.ChatToMessagesStream(parsed)
        inner = resp.response

        def gen():
            try:
                for chunk in inner:
                    out = conv.feed(chunk if isinstance(chunk, bytes) else str(chunk).encode())
                    if out:
                        yield out
                tail = conv.close()
                if tail:
                    yield tail
            finally:
                resp.close()  # the chat stream's own end: usage, limits, its trace
        out = Response(gen(), status=200, headers=hdrs, mimetype="text/event-stream")
        out.argos_traced = getattr(resp, "argos_traced", False)
        return out
    data = resp.get_data()
    resp.close()
    if resp.status_code >= 400:
        err = tr.error_to_responses(resp.status_code, data) if kind == "responses" else tr.error_to_messages(resp.status_code, data)
        out = Response(json.dumps(err), status=resp.status_code, headers=hdrs, mimetype="application/json")
    else:
        try:
            chat_answer = json.loads(data)
        except ValueError:
            chat_answer = {}
        if bool(chat.get("stream")):  # the model sent one answer although asked to stream: replay it as a stream
            conv = tr.ChatToResponsesStream(parsed) if kind == "responses" else tr.ChatToMessagesStream(parsed)
            out = Response(conv.feed(data) + conv.close(), status=200, headers=hdrs, mimetype="text/event-stream")
        else:
            ans = tr.chat_to_response(chat_answer, parsed) if kind == "responses" else tr.chat_to_message(chat_answer, parsed)
            out = Response(json.dumps(ans), status=200, headers=hdrs, mimetype="application/json")
    out.argos_traced = getattr(resp, "argos_traced", False)
    return out


def _convert_plan(engine_path: str) -> tuple[str, str, dict | None]:
    """(kind, why, request) when this request should be turned into a chat completion now; kind "" when not, and
    "maybe" when the engine is asked first (auto, a local model)."""
    kind = CONVERTED.get(engine_path.rstrip("/"))
    if not kind or request.method != "POST":
        return "", "", None
    try:
        parsed = json.loads(request.get_data() or b"null")
    except ValueError:
        return "", "", None
    if not isinstance(parsed, dict) or not parsed.get("model"):
        return "", "", None
    pb = current_app.extensions.get("pb")
    store = pb.store if pb is not None else None
    mode = convert_settings(store)[kind]
    if mode == "never":
        return "", "", None
    if mode == "always":
        return kind, "always", parsed
    from . import model_groups, providers
    provs = providers.listed(store) if store is not None else []
    model = str(parsed["model"])
    bare = model_groups.strip_prefix(model, {p["name"] for p in provs}) or model
    if providers.resolve(bare, provs):
        return kind, "a cloud provider's model", parsed
    return "maybe", kind, parsed


def _forward(engine_path: str) -> Response:
    """Forward a request (``_forward_inner``), and trace the model requests answered here rather than streamed."""
    t0 = time.time()
    kind, why, parsed = _convert_plan(engine_path)
    if kind and kind != "maybe":
        resp = _converted(engine_path, kind, parsed, why)
    else:
        resp = _forward_inner(engine_path)
        if kind == "maybe" and resp.status_code in (404, 405, 501):
            # the local model's server does not know this path: ask it in chat completions instead
            resp.close()
            g.pop("argos_trace", None)
            resp = _converted(engine_path, why, parsed, f"the model answered {resp.status_code} to {engine_path}")
    info = getattr(g, "argos_trace", None)
    if info and not getattr(resp, "argos_traced", False):
        err_text = ""
        if resp.status_code >= 400 and not resp.is_streamed:
            err_text = resp.get_data()[:500].decode("utf-8", "replace")
        _send_trace(_exporter(), info, t0, time.time(), resp.status_code, error=err_text, body=request.get_data())
    return resp


def _forward_inner(engine_path: str, body_override: bytes | None = None) -> Response:
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
    # which app behind the address or key (apps.py): the engine's loop guard and waiting line tell apps apart by it
    from . import apps as apps_mod
    app, app_how = apps_mod.identify(request.headers)
    for k in [k for k in headers if k.lower() == "x-argos-app"]:
        del headers[k]
    if app:
        # its UTF-8 bytes (http.client sends headers as Latin-1); the engine reads them as UTF-8
        headers["X-Argos-App"] = app.encode("utf-8").decode("latin-1")
    body = body_override if body_override is not None else _converted_audio(engine_path, headers)
    if body is None:
        body = request.get_data()
    if body_override is not None:
        headers["Content-Type"] = "application/json"
        for k in [k for k in headers if k.lower() in ("anthropic-version", "anthropic-beta")]:
            del headers[k]
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

    if request.method == "POST" and model:
        op = "embeddings" if "embedding" in engine_path else ("text_completion" if engine_path.rstrip("/").endswith(
            "/completions") and "chat" not in engine_path else "chat")
        g.argos_trace = {"model": model, "path": engine_path, "op": op, "key": key["name"] if key else "", "app": app,
                         "provider": cloud[0]["name"] if cloud else "",
                         "stream": bool(isinstance(parsed, dict) and parsed.get("stream"))}

    # ----- the key's rules, and the rules of the app behind it (gateway.py) -----
    groups = (store.get_settings().get(gateway.GROUPS_KEY) or {}) if (store is not None and key) else {}
    slot = False
    app_slot = ""
    arules = gateway.app_rules(key["rules"], app) if key else None
    who_app = f"the app {app} on this key"
    if key:
        rules = key["rules"]
        if arules and arules.get("blocked"):
            return _api_error(f"{who_app} is blocked", 403, "app_blocked")
        if model and not gateway.model_allowed(model, rules, groups):
            return _api_error(f"this API key may not use the model {model}", 403, "model_not_allowed")
        if model and arules and not gateway.model_allowed(model, arules, groups):
            return _api_error(f"{who_app} may not use the model {model}", 403, "model_not_allowed")
        if request.method == "POST" and model:
            day = time.strftime("%Y-%m-%d")
            why = LIMITER.acquire(key["id"], rules,
                                  gateway.tokens_used(store, key["id"], day) if rules.get("daily_tokens") else 0,
                                  gateway.tokens_used(store, key["id"], day[:8] + "01") if rules.get("monthly_tokens") else 0)
            if why:
                return _api_error(why, 429, "rate_limit_exceeded")
            slot = True
            if arules:
                need_day = arules.get("daily_tokens") or arules.get("daily_usd")
                need_month = arules.get("monthly_tokens") or arules.get("monthly_usd")
                used_d = gateway.app_used(store, key["id"], app, day) if need_day else (0, 0.0)
                used_m = gateway.app_used(store, key["id"], app, day[:8] + "01") if need_month else (0, 0.0)
                why = LIMITER.acquire(f"{key['id']}:{app}", arules, used_d[0], used_m[0], who=who_app)
                if why:
                    LIMITER.release(key["id"])
                    slot = False
                    return _api_error(why, 429, "rate_limit_exceeded")
                app_slot = f"{key['id']}:{app}"

    def done(tokens: int = 0) -> None:
        if slot:
            LIMITER.release(key["id"], tokens)
        if app_slot:
            LIMITER.release(app_slot, tokens)

    def budget_why() -> str:
        """"" while this key (and app) may spend money on a cloud model, else why not."""
        if not key or store is None:
            return ""
        r, day = key["rules"], time.strftime("%Y-%m-%d")
        if r.get("daily_usd") or r.get("monthly_usd"):
            why = gateway.budget_problem(gateway.usd_spent(store, key["id"], day) if r.get("daily_usd") else 0,
                                         gateway.usd_spent(store, key["id"], day[:8] + "01") if r.get("monthly_usd") else 0, r)
            if why:
                return why
        if arules and (arules.get("daily_usd") or arules.get("monthly_usd")):
            return gateway.budget_problem(gateway.app_used(store, key["id"], app, day)[1],
                                          gateway.app_used(store, key["id"], app, day[:8] + "01")[1], arules, who_app)
        return ""

    act: dict = {}

    def act_end() -> None:
        if act.pop("name", None) is not None:
            from .moe import ACTIVITY
            ACTIVITY.end(model)

    trace = getattr(g, "argos_trace", None)
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
                if cloud:
                    why = budget_why() if providers.price(cloud[0], cloud[1]) else ""
                    if why:
                        return _api_error(why, 429, "budget_exceeded")
                out, tokens = mcp_chat.run(pb, engine_url, parsed, headers, key, send=send, app=app)
                if key and store is not None:
                    cost = providers.cost(providers.price(cloud[0], cloud[1]), *tokens) if cloud else 0.0
                    _record(store, key, model, app, app_how, *tokens, cost_usd=cost)
                done(sum(tokens))
                slot, app_slot = False, ""
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
                        _record(store, key, model, app, app_how, 0, 0)
                    done(0)
                    slot, app_slot = False, ""
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
        price_now = None  # dollars per million tokens of whatever answers (a provider or fallback), for budgets
        HEALTH, retry = health(), gateway.retry_settings(store)
        is_call = request.method == "POST" and bool(model)  # a model request: worth a retry, counts for cooldown

        def pause(i: int, r=None) -> None:
            ra = (r.getheader("Retry-After") if r is not None else "") or ""
            time.sleep(min(5.0, float(ra)) if ra.replace(".", "", 1).isdigit() else 0.5 * (i + 1))

        if cloud:
            tgt = f"provider:{cloud[0]['name']}"
            price_now = providers.price(cloud[0], cloud[1])
            why = budget_why() if price_now and any(price_now) else ""
            if why:
                return _api_error(why, 429, "budget_exceeded")
            left = HEALTH.cooling(tgt)
            if left:
                return _api_error(f"the provider {cloud[0]['name']} failed {retry['fails']} times and cools down for "
                                  f"{left:.0f} s more", 503, "provider_cooling_down")
            for i in range(retry["retries"] + 1):
                try:
                    conn, resp = _to_fallback({"url": cloud[0]["url"], "api_key": cloud[0]["api_key"], "model": cloud[1]},
                                              engine_path, parsed, headers)
                except OSError as exc:
                    HEALTH.fail(tgt, str(exc), retry)
                    if i < retry["retries"] and not HEALTH.cooling(tgt):
                        pause(i)
                        continue
                    return _json(f"the provider {cloud[0]['name']} is unreachable: {exc}", 502)
                if resp.status in gateway.RETRY_STATUS:
                    err_body = resp.read()
                    conn.close()
                    HEALTH.fail(tgt, f"answered {resp.status}", retry)
                    if i < retry["retries"] and gateway.retryable(resp.status, err_body, False) and not HEALTH.cooling(tgt):
                        pause(i, resp)
                        continue
                    out_headers = [(k, v) for k, v in resp.getheaders() if k.lower() not in HOP_BY_HOP]
                    return Response(err_body, status=resp.status, headers=out_headers + [("X-Argos-Provider", cloud[0]["name"])])
                HEALTH.ok(tgt)
                break
        else:
            tgt = f"model:{model}"
            cool = HEALTH.cooling(tgt) if is_call else 0
            can_fb = bool(rule and rule.get("on_down"))
            if cool and can_fb:
                why_fb = f"it failed {retry['fails']} times and cools down for {cool:.0f} s more"
            else:
                for i in range(retry["retries"] + 1 if is_call else 1):
                    try:
                        conn, resp = _open(engine_url, request.method, target, body, headers, (rule or {}).get("wait_s") or 0,
                                           gone=_client_gone())
                    except ClientGone:
                        return _json("the app hung up before the answer started", 499)
                    except socket.timeout:
                        why_fb = f"no answer in {rule['wait_s']:g}s"
                        break
                    except OSError as exc:
                        if not can_fb:
                            return _json(f"engine unreachable: {exc}", 502)
                        why_fb = f"engine unreachable: {exc}"
                        break
                    if not is_call or resp.status < 400:
                        if is_call:
                            HEALTH.ok(tgt)
                        break
                    # an error is small: read it to see whether to try again, or to go to the fallback
                    err_body = resp.read()
                    conn.close()
                    again = gateway.retryable(resp.status, err_body, True)
                    if again:
                        HEALTH.fail(tgt, f"answered {resp.status}: {err_body[:120].decode('utf-8', 'replace')}", retry)
                    if again and i < retry["retries"] and not (HEALTH.cooling(tgt) and can_fb):
                        pause(i)
                        continue
                    if rule and ((resp.status >= 500 and rule.get("on_down")) or
                                 (rule.get("on_too_long") and gateway.too_long(resp.status, err_body))):
                        why_fb = f"the local model answered {resp.status}"
                        resp = None
                        break
                    out_headers = [(k, v) for k, v in resp.getheaders() if k.lower() not in HOP_BY_HOP]
                    return Response(err_body, status=resp.status, headers=out_headers)
        if resp is None:
            if not rule:
                return _json(f"the local model failed: {why_fb}", 502)
            price_now = tuple(rule["price"]) if rule.get("price") else None
            why = budget_why() if price_now else ""
            if why:
                return _api_error(f"the local model failed ({why_fb}) and the cloud fallback is not allowed: {why}",
                                  429, "budget_exceeded")
            try:
                conn, resp = _to_fallback(rule, engine_path, parsed, headers)
            except OSError as exc:
                return _json(f"the local model failed ({why_fb}) and so did the fallback {rule['name']}: {exc}", 502)
            fell_back = True
            if trace is not None:
                trace["fallback"] = rule["name"]
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
                if arules and arules.get("models"):
                    rows = [m for m in rows if gateway.model_allowed(str(m.get("id") or ""), arules, groups)]
                prefix = bool(store.get_settings().get(model_groups.PREFIX_KEY)) if store is not None else False
                listing["data"] = model_groups.arrange(rows, pnames, prefix)
                data = json.dumps(listing).encode()
            except (ValueError, AttributeError):
                pass
            return Response(data, status=status, headers=[(k, v) for k, v in out_headers if k.lower() != "content-length"])
        held, held_app = slot, app_slot
        slot, app_slot = False, ""  # the stream below releases them
        mine = dict(act)
        act.clear()  # and ends the model's activity

        exp, t_start, sent = _exporter(), time.time(), request.get_data() if trace else b""

        def price_end():
            # a provider's price from its model list may have arrived while it answered (fetched in the background)
            return price_now or (providers.price(cloud[0], cloud[1]) if cloud else None)

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
                    if key or keep_whole or trace is not None:
                        tail += chunk
                        del tail[:-131072]
                        if keep_whole and len(whole) < 4 * 2 ** 20:
                            whole.extend(chunk)
                    yield chunk
                if key and status < 400:
                    tokens = gateway.usage_from(bytes(tail), streamed)
                    _record(store, key, model, app, app_how, *tokens, cost_usd=providers.cost(price_end(), *tokens))
                if keep_whole and status == 200:
                    CACHE.put(cache_key, status, ctype, bytes(whole), cs["max_entries"])
            finally:
                conn.close()
                if trace is not None:
                    err_text = bytes(tail[-500:]).decode("utf-8", "replace") if status >= 400 else ""
                    _send_trace(exp, trace, t_start, time.time(), status,
                                tokens if (key or tokens != (0, 0)) else gateway.usage_from(bytes(tail), streamed),
                                providers.cost(price_end(), *tokens) if price_end() else None, err_text, sent,
                                _answer_text(bytes(tail), streamed))
                if held:
                    LIMITER.release(key["id"], sum(tokens))
                if held_app:
                    LIMITER.release(held_app, sum(tokens))
                if mine:
                    from .moe import ACTIVITY
                    ACTIVITY.end(model)

        out = Response(stream(), status=status, headers=out_headers)
        out.argos_traced = True  # the stream sends its own span when it ends
        return out
    finally:
        if slot or app_slot:
            done(0)
        act_end()


@bp.route("/engine/<path:subpath>", methods=METHODS)
def engine_direct(subpath: str):
    """The engine's own path, even where the builder has a route with the same name."""
    return _forward("/" + subpath)


@bp.route("/<path:subpath>", methods=METHODS)
def engine_fallback(subpath: str):
    return _forward("/" + subpath)
