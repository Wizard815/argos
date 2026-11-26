"""MCP tools in a chat request: Argos runs the tool calls itself.

An app adds an MCP entry to ``tools`` in a /v1/chat/completions request, the way OpenAI's Responses API names a
remote MCP server::

    {"type": "mcp", "server_url": "argos", "require_approval": "never"}          every server the key may use
    {"type": "mcp", "server_url": "argos/github", "require_approval": "never"}   one server
    {"type": "mcp", "server_label": "github", "allowed_tools": ["search"]}        one server, some tools

Argos swaps it for the servers' tools as plain functions, asks the model, runs the tools it calls, gives it the
results and asks again: up to ``max_tool_rounds`` rounds (5; at most 10), then once more without tools for the
answer. The answer carries ``argos_mcp`` with each call.

Only with ``require_approval: "never"`` on every MCP entry does Argos run anything. Otherwise the model's tool
calls go back to the app, which decides (it can run them through /mcp). A streamed request gets the final answer
as one streamed chunk once the rounds are done.
"""
from __future__ import annotations

import http.client
import json
import time
import urllib.parse
from typing import Any, Optional

from flask import Response

MAX_ROUNDS = 10


def wants_mcp(body: dict) -> bool:
    return any(isinstance(t, dict) and t.get("type") == "mcp" for t in body.get("tools") or [])


def _servers_for(entry: dict, gw, rules: Optional[dict]) -> list[str]:
    allowed = gw.reachable(rules)
    url = str(entry.get("server_url") or "argos").rstrip("/")
    label = str(entry.get("server_label") or "")
    one = ""
    if "/" in url and urllib.parse.urlsplit(url).path.rstrip("/").split("/")[-1] not in ("", "mcp", "argos"):
        one = urllib.parse.urlsplit(url).path.rstrip("/").split("/")[-1]
    elif url.startswith("argos/"):
        one = url.split("/", 1)[1]
    one = one or (label if label and label != "argos" else "")
    return [one] if one and one in allowed else ([] if one else allowed)


def _engine(engine_url: str, body: dict, headers: dict) -> tuple[int, dict, bytes]:
    parts = urllib.parse.urlsplit(engine_url)
    conn = http.client.HTTPConnection(parts.hostname, parts.port or 80, timeout=900)
    data = json.dumps(body).encode()
    h = {k: v for k, v in headers.items() if k.lower() not in ("content-length", "accept-encoding")}
    h.update({"Content-Type": "application/json", "Content-Length": str(len(data)), "Accept": "application/json"})
    try:
        conn.request("POST", "/v1/chat/completions", body=data, headers=h)
        resp = conn.getresponse()
        raw = resp.read()
    finally:
        conn.close()
    try:
        return resp.status, json.loads(raw), raw
    except ValueError:
        return resp.status, {}, raw


def run(pb, engine_url: str, body: dict, headers: dict, key: Optional[dict], send=None,
        app: str = "") -> tuple[Response, tuple[int, int]]:
    """``send(body) -> (status, json, raw)`` asks the model; the engine when None (a cloud provider's model otherwise).
    ``app``: the app behind the key (apps.py), for its own denied tools and the tool-call log."""
    from . import features
    from .gateway import tool_allowed
    from .mcp_gateway import MCPError, SEP, log_call, result_text
    if not features.on(pb.store, "mcp_tools"):  # off: no tool rules, no call log
        tool_allowed = lambda *a, **k: True  # noqa: E731
        log_call = lambda *a, **k: None  # noqa: E731
    gw = pb.mcp
    rules = key["rules"] if key else None
    body = json.loads(json.dumps(body))  # a copy we can change
    entries = [t for t in body["tools"] if isinstance(t, dict) and t.get("type") == "mcp"]
    other = [t for t in body["tools"] if not (isinstance(t, dict) and t.get("type") == "mcp")]
    auto = bool(entries) and all(str(e.get("require_approval") or "") == "never" for e in entries)
    stream = bool(body.pop("stream", False))
    body.pop("stream_options", None)
    try:
        rounds = max(0, min(MAX_ROUNDS, int(body.pop("max_tool_rounds", 5))))
    except (TypeError, ValueError):
        rounds = 5

    functions, owner, errors = [], {}, {}
    for e in entries:
        names = _servers_for(e, gw, rules)
        tools, errs = gw.all_tools(names)
        errors.update(errs)
        allow = set(e.get("allowed_tools") or [])
        for t in tools:
            server, tool = t["name"].split(SEP, 1)
            if allow and tool not in allow and t["name"] not in allow:
                continue
            if not tool_allowed(t["name"], rules, app):  # the key's (or app's) tool rules: never offered
                continue
            if t["name"] in owner:
                continue
            owner[t["name"]] = (server, tool)
            functions.append({"type": "function", "function": {
                "name": t["name"], "description": (t.get("description") or "")[:1024],
                "parameters": t.get("inputSchema") or {"type": "object", "properties": {}}}})
    body["tools"] = other + functions
    if not body["tools"]:
        body.pop("tools")
        body.pop("tool_choice", None)

    used_in, used_out = 0, 0
    calls: list[dict] = []
    status, answer, raw = 200, {}, b""
    for n in range(rounds + 1):
        last = n == rounds
        if last and functions:  # out of rounds: answer with what it has
            body.pop("tools", None)
            body.pop("tool_choice", None)
        status, answer, raw = send(body) if send else _engine(engine_url, body, headers)
        u = answer.get("usage") or {}
        used_in += int(u.get("prompt_tokens") or 0)
        used_out += int(u.get("completion_tokens") or 0)
        if status >= 400 or not answer.get("choices"):
            break
        msg = answer["choices"][0].get("message") or {}
        mine = [c for c in msg.get("tool_calls") or [] if (c.get("function") or {}).get("name") in owner]
        if not mine or not auto or last:
            break
        body["messages"] = list(body.get("messages") or []) + [msg]
        for c in msg.get("tool_calls") or []:
            name = (c.get("function") or {}).get("name") or ""
            if name not in owner:
                text = f"ERROR: {name} is not a tool Argos runs"
            else:
                server, tool = owner[name]
                t0 = time.time()
                raw_args = (c.get("function") or {}).get("arguments") or "{}"
                try:
                    args = json.loads(raw_args)
                    text = result_text(gw.call(server, tool, args if isinstance(args, dict) else {}))
                    ok = not text.startswith("ERROR:")
                except (MCPError, ValueError) as exc:
                    text, ok = f"ERROR: {exc}", False
                ms = round((time.time() - t0) * 1000)
                calls.append({"round": n + 1, "tool": name, "ok": ok, "ms": ms})
                log_call(pb.store, key, app, server, tool, "chat", ok, ms, "" if ok else text, raw_args)
            body["messages"].append({"role": "tool", "tool_call_id": c.get("id") or "", "content": text})
        pb.event("mcp", f"chat ran {len(mine)} MCP tool call(s) in round {n + 1}: "
                 + ", ".join(c["function"]["name"] for c in mine))

    if status >= 400 or not answer:
        return Response(raw, status=status or 502, mimetype="application/json"), (used_in, used_out)
    answer["usage"] = {**(answer.get("usage") or {}), "prompt_tokens": used_in, "completion_tokens": used_out,
                       "total_tokens": used_in + used_out}
    answer["argos_mcp"] = {"calls": calls, "servers_not_answering": errors, "ran_tools": auto}
    if not stream:
        return Response(json.dumps(answer), 200, mimetype="application/json"), (used_in, used_out)
    return Response(_as_events(answer), 200, mimetype="text/event-stream"), (used_in, used_out)


def _as_events(answer: dict) -> bytes:
    """The finished answer as a chat completion stream: one delta, the finish, the usage, [DONE]."""
    choice = (answer.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    base = {k: answer.get(k) for k in ("id", "created", "model")}
    base["object"] = "chat.completion.chunk"
    delta: dict[str, Any] = {"role": "assistant", "content": msg.get("content") or ""}
    if msg.get("reasoning_content"):
        delta["reasoning_content"] = msg["reasoning_content"]
    if msg.get("tool_calls"):
        delta["tool_calls"] = [{**c, "index": i} for i, c in enumerate(msg["tool_calls"])]
    events = [
        {**base, "choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
        {**base, "choices": [{"index": 0, "delta": {}, "finish_reason": choice.get("finish_reason") or "stop"}]},
        {**base, "choices": [], "usage": answer.get("usage"), "argos_mcp": answer.get("argos_mcp")},
    ]
    return b"".join(b"data: " + json.dumps(e).encode() + b"\n\n" for e in events) + b"data: [DONE]\n\n"
