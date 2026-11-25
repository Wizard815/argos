"""What an API key may do, and what happens to a model request on its way to the engine.

LiteLLM did this in front of Argos; now Argos does it itself (Settings -> Security, Settings -> Gateway):

- **Key rules**: each key can be limited to some models (names, ``*`` patterns or ``@group`` model groups), some
  MCP servers, an expiry date, and limits: requests a minute, tokens a minute, requests at once, tokens a day and
  a month. A blocked or expired key is refused like a wrong one. A key with no rules may do everything.
- **Usage per key**: every answered request adds its tokens to ``key_usage`` (per key, day and model), read from
  the answer's ``usage`` (or llama-server's ``timings``), streamed or not.
- **Answer cache**: a repeated request with temperature 0 (or an embedding) is answered from memory.
- **Cloud fallback**: when the local model is down, slow to start answering, or the prompt is too long for it, the
  request goes to another OpenAI-compatible server instead (rules in Settings -> Gateway).

The limits live in memory: one Argos, one process. Token totals per day and month come from the database.
"""
from __future__ import annotations

import collections
import fnmatch
import hashlib
import json
import threading
import time
from typing import Any, Iterable, Optional

GROUPS_KEY = "_model_groups"
CACHE_KEY = "_answer_cache"
FALLBACK_KEY = "_fallbacks"

RULE_FIELDS = ("models", "mcps", "mcp_mode", "expires_at", "blocked", "rpm", "tpm", "parallel", "daily_tokens",
               "monthly_tokens", "note")


# ----- key rules ------------------------------------------------------------------------------------------------
def clean_rules(d: dict) -> dict:
    """The rules a key keeps, checked and normalised. Unknown fields are dropped."""
    out: dict[str, Any] = {}
    models = d.get("models")
    if isinstance(models, str):
        models = [m for m in models.replace("\n", ",").split(",")]
    out["models"] = [str(m).strip() for m in (models or []) if str(m).strip()][:200]
    mode = str(d.get("mcp_mode") or "all")
    out["mcp_mode"] = mode if mode in ("all", "only", "except", "none") else "all"
    mcps = d.get("mcps")
    if isinstance(mcps, str):
        mcps = mcps.split(",")
    out["mcps"] = [str(m).strip() for m in (mcps or []) if str(m).strip()][:200]
    for k in ("rpm", "tpm", "parallel", "daily_tokens", "monthly_tokens"):
        try:
            v = int(float(d.get(k) or 0))
        except (TypeError, ValueError, OverflowError):
            raise ValueError(f"{k} must be a number") from None
        if v < 0:
            raise ValueError(f"{k} cannot be negative")
        out[k] = v
    try:
        out["expires_at"] = float(d.get("expires_at") or 0)
    except (TypeError, ValueError):
        raise ValueError("expires_at must be a time") from None
    out["blocked"] = bool(d.get("blocked"))
    out["note"] = str(d.get("note") or "")[:300]
    return out


def load_rules(text: Any) -> dict:
    if isinstance(text, dict):
        raw = text
    else:
        try:
            raw = json.loads(text or "{}")
        except ValueError:
            raw = {}
    try:
        return clean_rules(raw if isinstance(raw, dict) else {})
    except ValueError:
        return clean_rules({})


def usable(rules: dict, now: Optional[float] = None) -> str:
    """"" when a key with these rules may be used, else why not."""
    if rules.get("blocked"):
        return "this API key is blocked"
    if rules.get("expires_at") and (now or time.time()) >= rules["expires_at"]:
        return "this API key has expired"
    return ""


def expand(patterns: Iterable[str], groups: dict) -> list[str]:
    """Model patterns with each ``@group`` replaced by that group's patterns (one level; a group may not hold
    another)."""
    out = []
    for p in patterns:
        if p.startswith("@"):
            out.extend(str(x) for x in (groups.get(p[1:]) or []) if not str(x).startswith("@"))
        else:
            out.append(p)
    return out


def model_allowed(model: str, rules: dict, groups: dict) -> bool:
    """A key with no models may call any. A profile matches by the name apps see (its label) or its full id."""
    pats = expand(rules.get("models") or [], groups)
    if not rules.get("models"):
        return True
    if not model:
        return False
    names = {model}
    if "--" in model:
        names.add(model.split("--", 1)[1])
    return any(fnmatch.fnmatchcase(n, p) for n in names for p in pats)


def mcp_allowed(server: str, rules: Optional[dict]) -> bool:
    if not rules:
        return True
    mode, names = rules.get("mcp_mode") or "all", set(rules.get("mcps") or [])
    if mode == "none":
        return False
    if mode == "only":
        return server in names
    if mode == "except":
        return server not in names
    return True


# ----- limits -----------------------------------------------------------------------------------------------------
class Limiter:
    """Requests a minute, tokens a minute and requests at once, per key, in memory."""

    def __init__(self):
        self.lock = threading.Lock()
        self.reqs: dict[int, collections.deque] = {}
        self.toks: dict[int, collections.deque] = {}
        self.busy: dict[int, int] = {}

    def _trim(self, q: collections.deque, now: float) -> None:
        while q and q[0][0] < now - 60:
            q.popleft()

    def acquire(self, key_id: int, rules: dict, used_today: int = 0, used_month: int = 0,
                now: Optional[float] = None) -> str:
        """Take one request slot: "" when allowed, else why not (the caller answers 429)."""
        now = now or time.time()
        with self.lock:
            rq = self.reqs.setdefault(key_id, collections.deque())
            tq = self.toks.setdefault(key_id, collections.deque())
            self._trim(rq, now)
            self._trim(tq, now)
            if rules.get("rpm") and len(rq) >= rules["rpm"]:
                return f"rate limit: {rules['rpm']} requests a minute for this key"
            if rules.get("tpm") and sum(n for _, n in tq) >= rules["tpm"]:
                return f"rate limit: {rules['tpm']} tokens a minute for this key"
            if rules.get("parallel") and self.busy.get(key_id, 0) >= rules["parallel"]:
                return f"limit: {rules['parallel']} requests at once for this key"
            if rules.get("daily_tokens") and used_today >= rules["daily_tokens"]:
                return f"quota: {rules['daily_tokens']} tokens a day for this key, used up"
            if rules.get("monthly_tokens") and used_month >= rules["monthly_tokens"]:
                return f"quota: {rules['monthly_tokens']} tokens a month for this key, used up"
            rq.append((now, 1))
            self.busy[key_id] = self.busy.get(key_id, 0) + 1
            return ""

    def release(self, key_id: int, tokens: int = 0, now: Optional[float] = None) -> None:
        now = now or time.time()
        with self.lock:
            self.busy[key_id] = max(0, self.busy.get(key_id, 0) - 1)
            if tokens:
                self.toks.setdefault(key_id, collections.deque()).append((now, tokens))

    def view(self, key_id: int, now: Optional[float] = None) -> dict:
        now = now or time.time()
        with self.lock:
            rq, tq = self.reqs.get(key_id) or collections.deque(), self.toks.get(key_id) or collections.deque()
            self._trim(rq, now)
            self._trim(tq, now)
            return {"requests_last_minute": len(rq), "tokens_last_minute": sum(n for _, n in tq),
                    "in_flight": self.busy.get(key_id, 0)}


# ----- usage ------------------------------------------------------------------------------------------------------
def _find_usage(obj: Any) -> Optional[tuple[int, int]]:
    """(input, output) tokens from an answer: OpenAI and Anthropic ``usage``, or llama-server's ``timings``."""
    if isinstance(obj, dict):
        u = obj.get("usage")
        if isinstance(u, dict):
            i = u.get("prompt_tokens", u.get("input_tokens"))
            o = u.get("completion_tokens", u.get("output_tokens"))
            if isinstance(i, (int, float)) or isinstance(o, (int, float)):
                return int(i or 0), int(o or 0)
        t = obj.get("timings")
        if isinstance(t, dict) and ("prompt_n" in t or "predicted_n" in t):
            return int(t.get("prompt_n") or 0), int(t.get("predicted_n") or 0)
        for v in obj.values():
            if isinstance(v, (dict, list)):
                got = _find_usage(v)
                if got:
                    return got
    elif isinstance(obj, list):
        for v in obj:
            got = _find_usage(v)
            if got:
                return got
    return None


def usage_from(body: bytes, streamed: bool) -> tuple[int, int]:
    """Tokens in an answer. For a stream, the last event that carries them (``body`` is its tail)."""
    text = body.decode("utf-8", "replace")
    if not streamed:
        try:
            return _find_usage(json.loads(text)) or (0, 0)
        except ValueError:
            pass
    best = None
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if not data or data == "[DONE]":
            continue
        try:
            got = _find_usage(json.loads(data))
        except ValueError:
            continue
        if got:
            best = got
    return best or (0, 0)


def record_usage(store, key_id: int, model: str, tokens_in: int, tokens_out: int, now: Optional[float] = None) -> None:
    day = time.strftime("%Y-%m-%d", time.localtime(now or time.time()))
    store._x("INSERT INTO key_usage (key_id, day, model, requests, input_tokens, output_tokens) VALUES (?, ?, ?, 1, ?, ?) "
             "ON CONFLICT (key_id, day, model) DO UPDATE SET requests = key_usage.requests + 1, "
             "input_tokens = key_usage.input_tokens + excluded.input_tokens, "
             "output_tokens = key_usage.output_tokens + excluded.output_tokens",
             (key_id, day, model or "", tokens_in, tokens_out))


def tokens_used(store, key_id: int, since_day: str) -> int:
    rows = store._q("SELECT COALESCE(SUM(input_tokens + output_tokens), 0) AS n FROM key_usage WHERE key_id=? AND day>=?",
                    (key_id, since_day))
    return int((rows[0] or {}).get("n") or 0) if rows else 0


def usage_report(store, days: int = 30) -> list[dict]:
    since = time.strftime("%Y-%m-%d", time.localtime(time.time() - days * 86400))
    return store._q("SELECT key_id, day, model, requests, input_tokens, output_tokens FROM key_usage WHERE day>=? "
                    "ORDER BY day DESC, key_id, model", (since,))


# ----- answer cache -----------------------------------------------------------------------------------------------
CACHEABLE = ("/v1/chat/completions", "/v1/completions", "/v1/embeddings", "/v1/messages")


class AnswerCache:
    """Exact repeats only: same path and same request, temperature 0 (embeddings always), not streamed."""

    def __init__(self):
        self.lock = threading.Lock()
        self.items: "collections.OrderedDict[str, tuple[float, int, str, bytes]]" = collections.OrderedDict()
        self.hits = self.misses = 0

    @staticmethod
    def key_for(path: str, body: dict, settings: dict) -> str:
        if not settings.get("enabled") or path not in CACHEABLE or body.get("stream"):
            return ""
        if path != "/v1/embeddings":
            t = body.get("temperature")
            if t is None or float(t) != 0.0:
                return ""
        pats = settings.get("models") or []
        if pats and not any(fnmatch.fnmatchcase(str(body.get("model") or ""), p) for p in pats):
            return ""
        return hashlib.sha256((path + "\n" + json.dumps(body, sort_keys=True, separators=(",", ":"))).encode()).hexdigest()

    def get(self, key: str, ttl: float, now: Optional[float] = None) -> Optional[tuple[int, str, bytes]]:
        now = now or time.time()
        with self.lock:
            hit = self.items.get(key)
            if not hit or now - hit[0] > ttl:
                if hit:
                    del self.items[key]
                self.misses += 1
                return None
            self.items.move_to_end(key)
            self.hits += 1
            return hit[1], hit[2], hit[3]

    def put(self, key: str, status: int, ctype: str, data: bytes, limit: int, now: Optional[float] = None) -> None:
        if status != 200 or len(data) > 4 * 2 ** 20:
            return
        with self.lock:
            self.items[key] = (now or time.time(), status, ctype, data)
            self.items.move_to_end(key)
            while len(self.items) > max(1, limit):
                self.items.popitem(last=False)

    def view(self) -> dict:
        with self.lock:
            return {"entries": len(self.items), "hits": self.hits, "misses": self.misses}

    def clear(self) -> None:
        with self.lock:
            self.items.clear()


def cache_settings(store) -> dict:
    s = store.get_settings().get(CACHE_KEY) or {}
    return {"enabled": bool(s.get("enabled")), "ttl_s": float(s.get("ttl_s") or 3600),
            "max_entries": int(s.get("max_entries") or 500), "models": list(s.get("models") or [])}


# ----- cloud fallback ---------------------------------------------------------------------------------------------
def fallback_rules(store) -> list[dict]:
    return [r for r in (store.get_settings().get(FALLBACK_KEY) or []) if isinstance(r, dict)]


def clean_fallback(d: dict) -> dict:
    url = str(d.get("url") or "").strip().rstrip("/")
    if not url.startswith(("http://", "https://")):
        raise ValueError("the fallback URL must start with http:// or https://, e.g. https://api.openai.com/v1")
    match = str(d.get("match") or "").strip()
    if not match:
        raise ValueError("say which models it covers (a name or a pattern such as qwen*)")
    try:
        wait = max(0.0, float(d.get("wait_s") or 0))
    except (TypeError, ValueError):
        raise ValueError("wait_s must be a number of seconds") from None
    return {"name": str(d.get("name") or match).strip()[:60], "match": match, "url": url,
            "api_key": str(d.get("api_key") or ""), "model": str(d.get("model") or "").strip(),
            "on_down": d.get("on_down", True) is not False, "on_too_long": d.get("on_too_long", True) is not False,
            "wait_s": wait, "enabled": d.get("enabled", True) is not False}


def fallback_for(model: str, rules: list[dict]) -> Optional[dict]:
    names = {model}
    if "--" in model:
        names.add(model.split("--", 1)[1])
    for r in rules:
        if r.get("enabled", True) and any(fnmatch.fnmatchcase(n, r.get("match") or "") for n in names):
            return r
    return None


def too_long(status: int, body: bytes) -> bool:
    """llama-server's answer when a prompt does not fit the context."""
    if status not in (400, 413, 500):
        return False
    t = body[:4000].decode("utf-8", "replace").lower()
    return any(s in t for s in ("exceed", "context size", "context length", "too long", "n_ctx"))


def public_fallback(r: dict) -> dict:
    """A rule as the page sees it: the key is never sent back."""
    return {**{k: v for k, v in r.items() if k != "api_key"}, "has_key": bool(r.get("api_key"))}
