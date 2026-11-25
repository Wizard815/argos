"""Which app sent a request, for apps that share an address or an API key (all the apps behind LiteLLM, say).

The label is sent on to the engine as X-Argos-App: the loop guard then stops only the app that loops, the waiting
line gives each app its turn, and each API key lists the apps seen through it (Settings -> Security -> the key).

Where it comes from, first found:

1. ``X-Argos-App``: set by the app, or by LiteLLM per model (``extra_headers``).
2. LiteLLM's own key for the app: its alias, else its team's (``x-litellm-user_api_key_alias`` and
   ``..._team_alias``, sent when LiteLLM has ``litellm_settings: add_user_information_to_llm_headers: true`` and
   ``general_settings: forward_client_headers_to_llm_api: true``). Give each app its own LiteLLM key with an alias.
3. ``X-Title``: the app name OpenRouter-style apps send (forwarded by LiteLLM with the second setting above).
4. The User-Agent's product ("codex_cli_rs/0.40 (Linux)" -> "codex_cli_rs"). Behind LiteLLM this is LiteLLM's own
   ("AsyncOpenAI/Python"), the same for every app: then one of the above is needed.

A label is only a name an app gives itself: it tells honest apps apart, it is not a way to authenticate one.
"""
from __future__ import annotations

import re
import time

MAX_LEN = 60
MAX_APPS_PER_KEY = 200  # a key whose apps make up a new name each time stops growing its list here

# LiteLLM sends these with underscores in the name (x-litellm-user_api_key_alias). Waitress drops every header with
# an underscore (X_Forwarded_For would pass for X-Forwarded-For), so these few are let in with dashes instead.
LITELLM_HEADERS = ("x-litellm-user_api_key_alias", "x-litellm-user_api_key_team_alias",
                   "x-litellm-user_api_key_end_user_id")
_LITELLM_LINE = re.compile(rb"(?im)^(" + b"|".join(re.escape(h.encode()) for h in LITELLM_HEADERS) + rb")(?=[ \t]*:)")


def _clean(v) -> str:
    s = re.sub(r"[\x00-\x1f\x7f]+", " ", str(v or "")).strip()
    return s[:MAX_LEN]


def _product(ua: str) -> str:
    """The product in a User-Agent: "codex_cli_rs/0.40.0 (Linux; x86_64)" -> "codex_cli_rs"."""
    first = (ua or "").strip().split(" ", 1)[0]
    name = first.split("/", 1)[0] if "/" in first else first
    if name.lower() in ("mozilla",):  # a browser: the whole string says more than "Mozilla"
        return _clean(ua)
    # "AsyncOpenAI/Python 1.99": keep the language, it tells the SDKs apart
    m = re.match(r"^(Async)?OpenAI/(\w+)", ua or "")
    if m:
        return f"OpenAI/{m.group(2)}"
    return _clean(name)


def identify(headers) -> tuple[str, str]:
    """(app, how it was known) for a request's headers (a case-insensitive mapping); ("", "") when nothing says."""
    def h(name: str) -> str:
        return _clean(headers.get(name) or headers.get(name.replace("_", "-")) or "")

    for name, how in (("X-Argos-App", "X-Argos-App"),
                      ("x-litellm-user_api_key_alias", "LiteLLM key"),
                      ("x-litellm-user_api_key_team_alias", "LiteLLM team"),
                      ("X-Title", "X-Title")):
        v = h(name)
        if v:
            return v, how
    ua = _product(headers.get("User-Agent") or "")
    return (ua, "User-Agent") if ua else ("", "")


def patch_waitress() -> None:
    """Let LiteLLM's x-litellm-*_* headers through waitress, renamed with dashes (see LITELLM_HEADERS)."""
    try:
        from waitress import channel, parser
    except ImportError:
        return
    base = parser.HTTPRequestParser
    if getattr(base, "_argos_litellm", False):
        return

    class Parser(base):
        _argos_litellm = True

        def parse_header(self, header_plus):
            return super().parse_header(_LITELLM_LINE.sub(lambda m: m.group(1).replace(b"_", b"-"), header_plus))

    channel.HTTPChannel.parser_class = Parser


# ----- the apps seen through each key --------------------------------------------------------------------------
def record(store, key_id: int, app: str, how: str, model: str, tokens_in: int, tokens_out: int,
           now: float | None = None) -> None:
    if not app:
        return
    now = now or time.time()
    have = store._q("SELECT 1 AS x FROM key_apps WHERE key_id=? AND app=?", (key_id, app))
    if not have:
        n = store._q("SELECT COUNT(*) AS n FROM key_apps WHERE key_id=?", (key_id,))
        if n and int(n[0].get("n") or 0) >= MAX_APPS_PER_KEY:
            return
    store._x("INSERT INTO key_apps (key_id, app, how, requests, input_tokens, output_tokens, first_seen, last_seen, "
             "last_model) VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?) ON CONFLICT (key_id, app) DO UPDATE SET "
             "requests = key_apps.requests + 1, input_tokens = key_apps.input_tokens + excluded.input_tokens, "
             "output_tokens = key_apps.output_tokens + excluded.output_tokens, last_seen = excluded.last_seen, "
             "last_model = excluded.last_model, how = excluded.how",
             (key_id, app, how, tokens_in, tokens_out, now, now, model or ""))


def seen(store, key_id: int | None = None) -> list[dict]:
    """Apps seen through each key (or one key), the most recent first."""
    q = ("SELECT key_id, app, how, requests, input_tokens, output_tokens, first_seen, last_seen, last_model "
         "FROM key_apps")
    rows = store._q(q + (" WHERE key_id=?" if key_id is not None else "") + " ORDER BY last_seen DESC",
                    (key_id,) if key_id is not None else ())
    return [dict(r) for r in rows]


def forget(store, key_id: int, app: str | None = None) -> None:
    if app is None:
        store._x("DELETE FROM key_apps WHERE key_id=?", (key_id,))
    else:
        store._x("DELETE FROM key_apps WHERE key_id=? AND app=?", (key_id, app))
