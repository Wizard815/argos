"""Send each forwarded model request as a trace span to an observability tool.

OpenTelemetry OTLP over HTTP with JSON encoding (POST <endpoint>/v1/traces), because one
wire format reaches Langfuse, Phoenix, Jaeger, Grafana Tempo, SigNoz and any OpenTelemetry
Collector. Attributes follow the OpenTelemetry GenAI semantic conventions so those tools
show model, tokens and operation without extra mapping.

Prompts and answers are only sent when the settings say content=True: they can hold
anything a user typed, so the default keeps them on this machine.

Sending never blocks or raises into the request path: record() only appends to a
bounded queue, and a daemon thread posts batches every few seconds.
"""
from __future__ import annotations

import base64
import collections
import json
import random
import re
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable, Optional

SETTINGS_KEY = "_traces"
MASK = "••••"
CONTENT_MAX = 32 * 1024  # bytes of prompt or answer per span; collectors reject huge attributes
TIMEOUT_S = 10.0
LANGFUSE_CLOUD = "https://cloud.langfuse.com"
LANGFUSE_PATH = "/api/public/otel"
KINDS = ("otlp", "langfuse")
_HEADER_NAME = re.compile(r"^[A-Za-z0-9!#$%&'*+.^_`|~-]+$")
_HEX32 = re.compile(r"^[0-9a-f]{32}$")


# ---- settings ---------------------------------------------------------------

def _check_url(url: str, what: str = "Endpoint") -> str:
    url = (url or "").strip().rstrip("/")
    p = urllib.parse.urlparse(url)
    if p.scheme not in ("http", "https") or not p.netloc or any(c.isspace() for c in url):
        raise ValueError(f"{what} must be a full http:// or https:// URL, like https://collector.example.com:4318")
    return url


def _parse_headers(h) -> dict:
    """Headers from a dict or a "Name: value" per line string."""
    if h is None or h == "":
        return {}
    if isinstance(h, str):
        out = {}
        for line in h.splitlines():
            if not line.strip():
                continue
            if ":" not in line:
                raise ValueError(f'Header line "{line.strip()}" needs a colon, like "Name: value"')
            k, v = line.split(":", 1)
            out[k.strip()] = v.strip()
        return out
    if isinstance(h, dict):
        return {str(k).strip(): "" if v is None else str(v).strip() for k, v in h.items()}
    raise ValueError("Headers must be a list of \"Name: value\" lines")


def _old_value(old: dict, name: str) -> str:
    for k, v in old.items():
        if k.lower() == name.lower():
            return v
    return ""


def clean(d: dict, old: Optional[dict] = None) -> dict:
    """Validated trace settings. Raises ValueError with a message for the user.

    A header value or secret key sent back as "" or the mask keeps the old one, so a
    form that shows public() can be saved without retyping secrets.
    """
    d = d or {}
    old = old or {}
    kind = str(d.get("kind") or "otlp").strip().lower()
    if kind not in KINDS:
        raise ValueError(f'Kind must be one of: {", ".join(KINDS)}')
    try:
        sample = float(d.get("sample", 1.0) if d.get("sample") not in (None, "") else 1.0)
    except (TypeError, ValueError):
        raise ValueError("Sample must be a number from 0 to 1 (1 sends every request)") from None
    if not 0.0 <= sample <= 1.0 or sample != sample:
        raise ValueError("Sample must be a number from 0 to 1 (1 sends every request)")

    old_headers = old.get("headers") or {}
    headers = {}
    for k, v in _parse_headers(d.get("headers")).items():
        if not k:
            continue
        if not _HEADER_NAME.match(k):
            raise ValueError(f'"{k}" is not a valid header name')
        if v in ("", MASK):
            v = _old_value(old_headers, k)
            if not v:
                continue  # nothing to keep: a masked value with no old one is just dropped
        if "\r" in v or "\n" in v:
            raise ValueError(f'Header "{k}" cannot contain a line break')
        headers[k] = v

    out = {
        "enabled": bool(d.get("enabled")),
        "kind": kind,
        "endpoint": "",
        "headers": headers,
        "service_name": str(d.get("service_name") or "").strip() or "argos",
        "content": bool(d.get("content")),
        "sample": sample,
    }
    endpoint = str(d.get("endpoint") or "").strip()

    if kind == "langfuse":
        pub = str(d.get("public_key") or "").strip()
        sec = str(d.get("secret_key") or "").strip()
        if sec in ("", MASK):
            sec = old.get("secret_key", "")
        out["public_key"], out["secret_key"] = pub, sec
        base = _check_url(endpoint or LANGFUSE_CLOUD, "Langfuse URL")
        # A bare Langfuse URL (https://cloud.langfuse.com) gets its OTLP path added.
        if urllib.parse.urlparse(base).path in ("", "/"):
            base += LANGFUSE_PATH
        out["endpoint"] = base
        for k in [k for k in headers if k.lower() == "authorization"]:
            del headers[k]
        if pub and sec:
            headers["Authorization"] = "Basic " + base64.b64encode(f"{pub}:{sec}".encode()).decode()
        elif out["enabled"]:
            raise ValueError("Langfuse needs both the public key and the secret key")
    elif endpoint:
        out["endpoint"] = _check_url(endpoint)
    elif out["enabled"]:
        raise ValueError("Set the endpoint URL before turning traces on")
    return out


def public(settings: dict) -> dict:
    """The settings safe to show in the UI: header values and the secret key masked."""
    s = dict(settings or {})
    s["headers"] = {k: MASK for k in (s.get("headers") or {})}
    if s.get("secret_key"):
        s["secret_key"] = MASK
    return s


def traces_url(settings: dict) -> str:
    base = str((settings or {}).get("endpoint") or "").rstrip("/")
    return base if base.endswith("/v1/traces") else base + "/v1/traces"


# ---- spans ------------------------------------------------------------------

def _operation(path: str) -> str:
    p = (path or "").lower().rstrip("/")
    if p.endswith("embeddings"):
        return "embeddings"
    if p.endswith(("chat/completions", "responses", "messages")):
        return "chat"
    if p.endswith("completions"):
        return "text_completion"
    return "chat"


def _nanos(t: float) -> str:
    # Microseconds first: a float epoch has no real nanosecond digits, only rounding noise.
    return str(int(round(t * 1e6)) * 1000)


def span(*, name: str, start: float, end: float, model: str, path: str, status: int, key: str = "",
         app: str = "", provider: str = "", input_tokens: Optional[int] = None,
         output_tokens: Optional[int] = None, cost_usd: Optional[float] = None, stream: bool = False,
         error: str = "", fallback: str = "", request_body: Optional[bytes] = None,
         response_text: Optional[str] = None, trace_id: Optional[str] = None) -> dict:
    """One request as an internal span dict; Exporter.record() turns it into OTLP.

    request_body and response_text ride along under private keys and only become
    attributes when the settings allow content.
    """
    tid = (trace_id or "").lower()
    attrs = {
        "gen_ai.system": provider or "argos",
        "gen_ai.operation.name": _operation(path),
        "gen_ai.request.model": model or "",
        "http.response.status_code": int(status or 0),
        "url.path": path or "",
        "argos.stream": bool(stream),
    }
    if input_tokens is not None:
        attrs["gen_ai.usage.input_tokens"] = int(input_tokens)
    if output_tokens is not None:
        attrs["gen_ai.usage.output_tokens"] = int(output_tokens)
    if cost_usd is not None:
        attrs["argos.cost_usd"] = float(cost_usd)
    for k, v in (("argos.key", key), ("argos.app", app), ("argos.fallback", fallback)):
        if v:
            attrs[k] = str(v)
    failed = (status or 0) >= 400 or bool(error)
    st = {"code": 2, "message": error or f"HTTP {status}"} if failed else {"code": 1}
    return {
        "traceId": tid if _HEX32.match(tid) else secrets.token_hex(16),
        "spanId": secrets.token_hex(8),
        "name": name or f"{attrs['gen_ai.operation.name']} {model}".strip(),
        "kind": 3,  # CLIENT: Argos calls the model on the user's behalf
        "startTimeUnixNano": _nanos(start),
        "endTimeUnixNano": _nanos(max(end, start)),
        "status": st,
        "attributes": attrs,
        "_prompt": request_body,
        "_completion": response_text,
    }


def _cut(v) -> str:
    """Text cut to CONTENT_MAX bytes, never splitting a UTF-8 character."""
    b = v if isinstance(v, (bytes, bytearray)) else str(v).encode("utf-8", "replace")
    if len(b) <= CONTENT_MAX:
        return bytes(b).decode("utf-8", "replace")
    return bytes(b[:CONTENT_MAX]).decode("utf-8", "ignore") + " …(cut)"


def _value(v) -> dict:
    if isinstance(v, bool):  # before int: bool is an int in Python
        return {"boolValue": v}
    if isinstance(v, int):
        return {"intValue": str(v)}  # OTLP JSON sends 64-bit ints as strings
    if isinstance(v, float):
        return {"doubleValue": v}
    return {"stringValue": str(v)}


def _attrs(d: dict) -> list:
    return [{"key": k, "value": _value(v)} for k, v in d.items()]


def to_otlp(s: dict, content: bool) -> dict:
    """An internal span as an OTLP JSON span. Content is added only when allowed."""
    attrs = dict(s.get("attributes") or {})
    if content:
        if s.get("_prompt") is not None:
            attrs["gen_ai.prompt"] = attrs["langfuse.observation.input"] = _cut(s["_prompt"])
        if s.get("_completion") is not None:
            attrs["gen_ai.completion"] = attrs["langfuse.observation.output"] = _cut(s["_completion"])
    out = {k: v for k, v in s.items() if not k.startswith("_") and k != "attributes"}
    out["attributes"] = _attrs(attrs)
    return out


def payload(settings: dict, spans: list) -> dict:
    return {"resourceSpans": [{
        "resource": {"attributes": _attrs({"service.name": settings.get("service_name") or "argos"})},
        "scopeSpans": [{"scope": {"name": "argos"}, "spans": spans}],
    }]}


def _post(settings: dict, spans: list) -> str:
    """POST spans; "" on success or the error text. Never raises."""
    url = traces_url(settings)
    try:
        body = json.dumps(payload(settings, spans)).encode()
        req = urllib.request.Request(url, data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("User-Agent", "argos-traces")
        for k, v in (settings.get("headers") or {}).items():
            req.add_header(k, v)
        # A fresh opener reads HTTPS_PROXY/HTTP_PROXY/NO_PROXY now, not when the process started.
        with urllib.request.build_opener().open(req, timeout=TIMEOUT_S) as r:
            r.read()
        return ""
    except urllib.error.HTTPError as e:
        try:
            detail = e.read(300).decode("utf-8", "replace").strip()
        except Exception:
            detail = ""
        return f"{url} answered HTTP {e.code}" + (f": {detail}" if detail else "")
    except urllib.error.URLError as e:
        return f"Cannot reach {url}: {e.reason}"
    except Exception as e:  # timeouts, bad headers, anything else: report, never raise
        return f"Sending to {url} failed: {e}"


def test_send(settings: dict) -> str:
    """Send one test span now, enabled or not; "" on success or the error text."""
    if not (settings or {}).get("endpoint"):
        return "Set the endpoint URL first"
    now = time.time()
    s = span(name="argos test span", start=now - 0.05, end=now, model="argos-test", path="/v1/chat/completions",
             status=200, request_body=b'{"messages":[{"role":"user","content":"test"}]}', response_text="ok")
    return _post(settings, [to_otlp(s, bool(settings.get("content")))])


# ---- exporter ---------------------------------------------------------------

class Exporter:
    """Queues spans and posts them in batches from a daemon thread."""

    def __init__(self, get_settings: Callable[[], dict], flush_s: float = 2.0, max_queue: int = 2000,
                 max_batch: int = 200):
        self._get = get_settings
        self.flush_s = flush_s
        self.max_queue = max_queue
        self.max_batch = max(1, max_batch)
        self._q: collections.deque = collections.deque()  # (otlp span, tries so far)
        self._lock = threading.Lock()
        self._send_lock = threading.Lock()  # the thread and flush() never post the same spans twice
        self._stop = threading.Event()
        self._stats = {"sent": 0, "dropped": 0, "failed": 0, "last_error": "", "last_sent_at": 0.0}
        self._thread = threading.Thread(target=self._run, name="argos-traces", daemon=True)
        self._thread.start()

    def _settings(self) -> dict:
        try:
            return self._get() or {}
        except Exception:
            return {}

    def record(self, span: dict) -> None:
        """Queue a span. Never blocks or raises; counts a drop when it cannot be kept."""
        try:
            st = self._settings()
            ok = st.get("enabled") and st.get("endpoint")
            sample = float(st.get("sample", 1.0))
            if not ok or (sample < 1.0 and random.random() >= sample):
                self._count("dropped")
                return
            # Converted now so the prompt and answer are not kept when content is off.
            item = (to_otlp(span, bool(st.get("content"))), 0)
            with self._lock:
                if len(self._q) >= self.max_queue:
                    self._stats["dropped"] += 1
                    return
                self._q.append(item)
        except Exception:
            self._count("dropped")

    def _count(self, k: str, n: int = 1) -> None:
        with self._lock:
            self._stats[k] += n

    def flush(self) -> None:
        """Post everything queued right now. A failed batch is retried on the next flush, then dropped."""
        with self._send_lock:
            with self._lock:
                items = list(self._q)
                self._q.clear()
            if not items:
                return
            st = self._settings()
            if not (st.get("enabled") and st.get("endpoint")):
                self._count("dropped", len(items))  # turned off while spans waited
                return
            retry = []
            for i in range(0, len(items), self.max_batch):
                batch = items[i:i + self.max_batch]
                err = _post(st, [s for s, _ in batch])
                with self._lock:
                    if not err:
                        self._stats["sent"] += len(batch)
                        self._stats["last_sent_at"] = time.time()
                        continue
                    self._stats["last_error"] = err
                    for s, tries in batch:
                        if tries:
                            self._stats["failed"] += 1
                        else:
                            retry.append((s, 1))
            if retry:
                with self._lock:
                    # Retries go first; whatever no longer fits is dropped as failed.
                    room = max(0, self.max_queue - len(self._q))
                    self._stats["failed"] += max(0, len(retry) - room)
                    self._q.extendleft(reversed(retry[:room]))

    def stats(self) -> dict:
        with self._lock:
            return {**self._stats, "queued": len(self._q)}

    def close(self) -> None:
        """Stop the thread and send what is left (one try)."""
        self._stop.set()
        self._thread.join(timeout=TIMEOUT_S + 1)
        try:
            self.flush()
        except Exception:
            pass

    def _run(self) -> None:
        while not self._stop.wait(self.flush_s):
            try:
                if self._q:
                    self.flush()
            except Exception as e:  # keep the thread alive whatever happens
                with self._lock:
                    self._stats["last_error"] = f"Trace exporter error: {e}"
