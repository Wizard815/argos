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
               "monthly_tokens", "note", "daily_usd", "monthly_usd", "mcp_tools", "mcp_tools_deny", "apps")
APP_FIELDS = ("models", "rpm", "tpm", "parallel", "daily_tokens", "monthly_tokens", "daily_usd", "monthly_usd",
              "mcp_tools_deny", "blocked", "note")
MAX_APP_RULES = 100


def _patterns(v) -> list[str]:
    if isinstance(v, str):
        v = v.replace("\n", ",").split(",")
    return [str(m).strip() for m in (v or []) if str(m).strip()][:200]


def _usd(d: dict, k: str) -> float:
    try:
        v = float(d.get(k) or 0)
    except (TypeError, ValueError, OverflowError):
        raise ValueError(f"{k} must be an amount in dollars") from None
    if not (v >= 0) or v > 1e7:  # also NaN
        raise ValueError(f"{k} is from 0 to 10,000,000 dollars")
    return round(v, 4)


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
    # dollars: what cloud models (providers, fallbacks) may cost; local models cost nothing and are never refused
    out["daily_usd"] = _usd(d, "daily_usd")
    out["monthly_usd"] = _usd(d, "monthly_usd")
    # MCP tools as <server>__<tool> patterns: only these (blank: every tool of the servers it may use), never these
    out["mcp_tools"] = _patterns(d.get("mcp_tools"))
    out["mcp_tools_deny"] = _patterns(d.get("mcp_tools_deny"))
    # rules for one app behind this key (apps.py names it): the same limits, on top of the key's
    apps = d.get("apps") or {}
    if not isinstance(apps, dict):
        raise ValueError("apps is {app name: rules}")
    out["apps"] = {}
    for name, r in list(apps.items())[:MAX_APP_RULES]:
        name = str(name).strip()[:60]
        if not name or not isinstance(r, dict):
            continue
        try:
            c = clean_rules({k: v for k, v in r.items() if k in APP_FIELDS})
        except ValueError as exc:
            raise ValueError(f"app {name}: {exc}") from None
        out["apps"][name] = {k: c[k] for k in APP_FIELDS}
    return out


def app_rules(rules: dict, app: str) -> Optional[dict]:
    """The rules for one app under a key, or None."""
    return (rules.get("apps") or {}).get(app) if app else None


def tool_allowed(full_name: str, rules: Optional[dict], app: str = "") -> bool:
    """May a key (and the app behind it) use the MCP tool <server>__<tool>?"""
    if not rules:
        return True
    allow = rules.get("mcp_tools") or []
    if allow and not any(fnmatch.fnmatchcase(full_name, p) for p in allow):
        return False
    deny = list(rules.get("mcp_tools_deny") or []) + list((app_rules(rules, app) or {}).get("mcp_tools_deny") or [])
    return not any(fnmatch.fnmatchcase(full_name, p) for p in deny)


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

    def acquire(self, key_id, rules: dict, used_today: int = 0, used_month: int = 0,
                now: Optional[float] = None, who: str = "this key") -> str:
        """Take one request slot: "" when allowed, else why not (the caller answers 429). ``key_id`` is a key's id,
        or "<id>:<app>" for an app's own limits."""
        now = now or time.time()
        with self.lock:
            rq = self.reqs.setdefault(key_id, collections.deque())
            tq = self.toks.setdefault(key_id, collections.deque())
            self._trim(rq, now)
            self._trim(tq, now)
            if rules.get("rpm") and len(rq) >= rules["rpm"]:
                return f"rate limit: {rules['rpm']} requests a minute for {who}"
            if rules.get("tpm") and sum(n for _, n in tq) >= rules["tpm"]:
                return f"rate limit: {rules['tpm']} tokens a minute for {who}"
            if rules.get("parallel") and self.busy.get(key_id, 0) >= rules["parallel"]:
                return f"limit: {rules['parallel']} requests at once for {who}"
            if rules.get("daily_tokens") and used_today >= rules["daily_tokens"]:
                return f"quota: {rules['daily_tokens']} tokens a day for {who}, used up"
            if rules.get("monthly_tokens") and used_month >= rules["monthly_tokens"]:
                return f"quota: {rules['monthly_tokens']} tokens a month for {who}, used up"
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


def record_usage(store, key_id: int, model: str, tokens_in: int, tokens_out: int, now: Optional[float] = None,
                 cost_usd: float = 0.0) -> None:
    day = time.strftime("%Y-%m-%d", time.localtime(now or time.time()))
    store._x("INSERT INTO key_usage (key_id, day, model, requests, input_tokens, output_tokens, cost_usd) "
             "VALUES (?, ?, ?, 1, ?, ?, ?) "
             "ON CONFLICT (key_id, day, model) DO UPDATE SET requests = key_usage.requests + 1, "
             "input_tokens = key_usage.input_tokens + excluded.input_tokens, "
             "output_tokens = key_usage.output_tokens + excluded.output_tokens, "
             "cost_usd = COALESCE(key_usage.cost_usd, 0) + excluded.cost_usd",
             (key_id, day, model or "", tokens_in, tokens_out, float(cost_usd or 0)))


def tokens_used(store, key_id: int, since_day: str) -> int:
    rows = store._q("SELECT COALESCE(SUM(input_tokens + output_tokens), 0) AS n FROM key_usage WHERE key_id=? AND day>=?",
                    (key_id, since_day))
    return int((rows[0] or {}).get("n") or 0) if rows else 0


def usd_spent(store, key_id: int, since_day: str) -> float:
    rows = store._q("SELECT COALESCE(SUM(cost_usd), 0) AS n FROM key_usage WHERE key_id=? AND day>=?", (key_id, since_day))
    return float((rows[0] or {}).get("n") or 0) if rows else 0.0


def app_used(store, key_id: int, app: str, since_day: str) -> tuple[int, float]:
    """(tokens, dollars) one app used through a key since a day."""
    rows = store._q("SELECT COALESCE(SUM(input_tokens + output_tokens), 0) AS n, COALESCE(SUM(cost_usd), 0) AS c "
                    "FROM app_usage WHERE key_id=? AND app=? AND day>=?", (key_id, app, since_day))
    r = rows[0] if rows else {}
    return int(r.get("n") or 0), float(r.get("c") or 0)


def budget_problem(spent_day: float, spent_month: float, rules: dict, who: str = "this key") -> str:
    """"" while a request that costs money may go, else why not."""
    if rules.get("daily_usd") and spent_day >= rules["daily_usd"]:
        return f"budget: ${rules['daily_usd']:g} a day for {who} is spent (${spent_day:.2f}); local models still answer"
    if rules.get("monthly_usd") and spent_month >= rules["monthly_usd"]:
        return f"budget: ${rules['monthly_usd']:g} a month for {who} is spent (${spent_month:.2f}); local models still answer"
    return ""


def usage_report(store, days: int = 30) -> list[dict]:
    since = time.strftime("%Y-%m-%d", time.localtime(time.time() - days * 86400))
    return store._q("SELECT key_id, day, model, requests, input_tokens, output_tokens, COALESCE(cost_usd, 0) AS cost_usd "
                    "FROM key_usage WHERE day>=? "
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
            "wait_s": wait, "enabled": d.get("enabled", True) is not False, "price": _fallback_price(d.get("price"))}


def _fallback_price(v) -> list:
    """[input, output] dollars per million tokens at the fallback, for dollar budgets; [] when free or unknown."""
    if not v:
        return []
    if isinstance(v, str):
        v = v.split(",")
    try:
        a, b = (list(v) + [0, 0])[:2]
        a, b = float(a or 0), float(b or 0)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("price is two numbers: dollars per million tokens in, out") from None
    if not (0 <= a <= 10000 and 0 <= b <= 10000):
        raise ValueError("price is from 0 to 10,000 dollars per million tokens")
    return [a, b] if (a or b) else []


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


# ----- retries and cooldown ---------------------------------------------------------------------------------------
RETRY_KEY = "_retry"
RETRY_DEFAULTS = {"retries": 1, "fails": 3, "window_s": 60, "cooldown_s": 60}
RETRY_STATUS = (429, 500, 502, 503, 504)


def retry_settings(store) -> dict:
    v = (store.get_settings().get(RETRY_KEY) if store is not None else None) or {}
    try:
        return clean_retry({**RETRY_DEFAULTS, **v})
    except ValueError:
        return dict(RETRY_DEFAULTS)


def clean_retry(d: dict) -> dict:
    out = {}
    for k, lo, hi in (("retries", 0, 3), ("fails", 1, 100), ("window_s", 5, 3600), ("cooldown_s", 0, 3600)):
        try:
            v = int(float(d.get(k, RETRY_DEFAULTS[k])))
        except (TypeError, ValueError, OverflowError):
            raise ValueError(f"{k} must be a number") from None
        if not lo <= v <= hi:
            raise ValueError(f"{k} is from {lo} to {hi}")
        out[k] = v
    return out


def retryable(status: int, body: bytes, local: bool) -> bool:
    """Worth sending again: the server failed before answering, for a reason that may pass. Not a request Argos
    itself stopped (cancelled, the loop guard), nor a model the engine stopped starting after crashes. A local
    model's 429 is Argos's own limit or the loop guard: never retried."""
    if status not in RETRY_STATUS or (local and status == 429):
        return False
    t = body[:4000].lower()
    return not any(w in t for w in (b"cancelled", b"canceled", b"loop guard", b"crashed", b"shutting down",
                                    b"not started again"))


class Health:
    """Failures per target (a local model, a provider): ``fails`` within ``window_s`` cool it down for
    ``cooldown_s``. While a local model cools down its requests go straight to its cloud fallback (if any); a
    cooling provider is answered 503 at once instead of being asked again."""

    def __init__(self):
        self.lock = threading.Lock()
        self.fails: dict[str, collections.deque] = {}
        self.until: dict[str, float] = {}
        self.why: dict[str, str] = {}

    def fail(self, target: str, why: str, settings: dict, now: Optional[float] = None) -> bool:
        """Count a failure; True when it starts a cooldown."""
        now = time.time() if now is None else now
        with self.lock:
            q = self.fails.setdefault(target, collections.deque())
            q.append(now)
            while q and q[0] < now - settings["window_s"]:
                q.popleft()
            self.why[target] = why[:300]
            if settings["cooldown_s"] and len(q) >= settings["fails"] and self.until.get(target, 0) <= now:
                self.until[target] = now + settings["cooldown_s"]
                q.clear()
                return True
        return False

    def ok(self, target: str) -> None:
        with self.lock:
            self.fails.pop(target, None)
            self.until.pop(target, None)

    def cooling(self, target: str, now: Optional[float] = None) -> float:
        """Seconds left of its cooldown, 0 when none."""
        now = time.time() if now is None else now
        with self.lock:
            return max(0.0, self.until.get(target, 0) - now)

    def view(self, now: Optional[float] = None) -> list[dict]:
        now = time.time() if now is None else now
        with self.lock:
            return [{"target": t, "seconds_left": round(u - now), "why": self.why.get(t, "")}
                    for t, u in sorted(self.until.items()) if u > now]

    def reset(self, target: Optional[str] = None) -> None:
        with self.lock:
            if target is None:
                self.fails.clear()
                self.until.clear()
            else:
                self.fails.pop(target, None)
                self.until.pop(target, None)
