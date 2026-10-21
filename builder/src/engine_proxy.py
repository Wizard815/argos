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
import urllib.parse

from flask import Blueprint, Response, abort, current_app, request

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
    import json
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


def _forward(engine_path: str) -> Response:
    engine_url = current_app.config.get("ENGINE_URL") or ""
    if not engine_url:
        abort(404)  # builder-only run: unknown paths get the builder's own 404
    if _blocked(engine_path):
        return _json("not found", 404)

    parts = urllib.parse.urlsplit(engine_url)
    https = parts.scheme == "https"
    conn_cls = http.client.HTTPSConnection if https else http.client.HTTPConnection
    conn = conn_cls(parts.hostname, parts.port or (443 if https else 80), timeout=600)

    query = request.query_string.decode("latin-1")
    target = engine_path + (f"?{query}" if query else "")
    headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP_BY_HOP}
    headers["X-Forwarded-For"] = request.remote_addr or ""
    headers["X-Forwarded-Host"] = request.host
    body = _converted_audio(engine_path, headers)
    if body is None:
        body = request.get_data()
    if body:
        headers["Content-Length"] = str(len(body))

    try:
        conn.request(request.method, target, body=body or None, headers=headers)
        resp = conn.getresponse()
    except OSError as exc:
        conn.close()
        return _json(f"engine unreachable: {exc}", 502)

    out_headers = [(k, v) for k, v in resp.getheaders() if k.lower() not in HOP_BY_HOP]

    def stream():
        try:
            while True:
                # read1 returns as soon as bytes arrive, so server-sent events stay live
                chunk = resp.read1(65536)
                if not chunk:
                    break
                yield chunk
        finally:
            conn.close()

    return Response(stream(), status=resp.status, headers=out_headers)


@bp.route("/engine/<path:subpath>", methods=METHODS)
def engine_direct(subpath: str):
    """The engine's own path, even where the builder has a route with the same name."""
    return _forward("/" + subpath)


@bp.route("/<path:subpath>", methods=METHODS)
def engine_fallback(subpath: str):
    return _forward("/" + subpath)
