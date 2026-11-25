"""The MCP gateway's addresses (/mcp and /mcp/<server>) and Settings -> MCP (see mcp_gateway.py)."""
from __future__ import annotations

import json

from flask import Blueprint, Response, g, request

from ..mcp_gateway import SEP, SERVED_VERSIONS, MCPError, clean_server
from ..web import body, ctx, err, ok
from .security import _may_change

bp = Blueprint("mcp_gateway", __name__)


def _rpc(id_, result=None, error=None) -> dict:
    out = {"jsonrpc": "2.0", "id": id_}
    if error is not None:
        out["error"] = error
    else:
        out["result"] = result
    return out


def _handle(msg: dict, one: str, names: list[str]) -> dict | None:
    gw = ctx().mcp
    method, mid, params = msg.get("method"), msg.get("id"), msg.get("params") or {}
    if mid is None:  # a notification: nothing to answer
        return None
    if method == "initialize":
        asked = str(params.get("protocolVersion") or "")
        return _rpc(mid, {"protocolVersion": asked if asked in SERVED_VERSIONS else SERVED_VERSIONS[0],
                          "capabilities": {"tools": {"listChanged": False}},
                          "serverInfo": {"name": f"argos-mcp{'-' + one if one else ''}", "version": "1"},
                          "instructions": (f"The tools of the MCP server {one}, through Argos." if one else
                                           f"Tools of {len(names)} MCP servers through Argos, named <server>{SEP}<tool>.")})
    if method == "ping":
        return _rpc(mid, {})
    if method == "tools/list":
        tools, _ = gw.all_tools(names, prefixed=not one)
        return _rpc(mid, {"tools": tools})
    if method == "tools/call":
        name = str(params.get("name") or "")
        try:
            server, tool = (one, name) if one else gw.split(name)
            if server not in names:
                raise MCPError(f"no MCP server {server} for this key")
            return _rpc(mid, gw.call(server, tool, params.get("arguments") or {}))
        except MCPError as exc:
            return _rpc(mid, {"content": [{"type": "text", "text": str(exc)}], "isError": True})
    if method in ("resources/list", "prompts/list"):
        return _rpc(mid, {method.split("/")[0]: []})
    return _rpc(mid, error={"code": -32601, "message": f"{method} is not supported here"})


def _serve(one: str = ""):
    if request.method == "GET":  # no server-to-client stream: every answer comes back on its POST
        return Response(status=405, headers={"Allow": "POST, DELETE"})
    if request.method == "DELETE":
        return Response(status=200)
    gw = ctx().mcp
    rules = (g.get("api_key") or {}).get("rules")
    if one:
        if not gw.server(one):
            return Response(json.dumps(_rpc(None, error={"code": -32001, "message": f"no MCP server {one}"})), 404,
                            mimetype="application/json")
        if one not in gw.reachable(rules):
            return Response(json.dumps(_rpc(None, error={"code": -32001, "message": f"this API key may not use {one}"})),
                            403, mimetype="application/json")
        names = [one]
    else:
        split = lambda v: {x.strip() for x in (v or "").split(",") if x.strip()}  # noqa: E731
        names = gw.reachable(rules, split(request.args.get("only")), split(request.args.get("skip")))
    try:
        msg = json.loads(request.get_data() or b"null")
    except ValueError:
        return Response(json.dumps(_rpc(None, error={"code": -32700, "message": "not JSON"})), 400, mimetype="application/json")
    if isinstance(msg, list):
        out = [r for r in (_handle(m, one, names) for m in msg if isinstance(m, dict)) if r]
        return Response(json.dumps(out), 200, mimetype="application/json") if out else Response(status=202)
    if not isinstance(msg, dict):
        return Response(json.dumps(_rpc(None, error={"code": -32600, "message": "not a JSON-RPC message"})), 400,
                        mimetype="application/json")
    got = _handle(msg, one, names)
    return Response(json.dumps(got), 200, mimetype="application/json") if got else Response(status=202)


@bp.route("/mcp", methods=["GET", "POST", "DELETE"])
def mcp_all():
    return _serve("")


@bp.route("/mcp/<name>", methods=["GET", "POST", "DELETE"])
def mcp_one(name):
    return _serve(name)


# ----- Settings -> MCP ---------------------------------------------------------------------------------------------
@bp.get("/api/mcp/servers")
def mcp_servers():
    return ok(servers=ctx().mcp.view(), separator=SEP)


@bp.post("/api/mcp/servers")
def mcp_server_save():
    """Add a server, or change one (``replace``: its old name). Header and env values left as •••• are kept."""
    if not _may_change():
        return err("log in to change this", 401)
    d = body()
    gw = ctx().mcp
    old_name = str(d.get("replace") or "")
    old = gw.server(old_name) if old_name else None
    if old:
        for k in ("headers", "env"):
            if isinstance(d.get(k), dict):
                d[k] = {n: (old.get(k, {}).get(n, "") if v == "••••" else v) for n, v in d[k].items()}
    try:
        s = clean_server(d)
    except ValueError as exc:
        return err(str(exc))
    raw = [x for x in (ctx().store.get_settings().get("_mcp_servers") or []) if x.get("name") != old_name]
    if any(x.get("name") == s["name"] for x in raw):
        return err(f"there is already an MCP server called {s['name']}")
    gw.save(raw + [s])
    ctx().event("settings", f"MCP server {s['name']} saved")
    return ok(servers=gw.view())


@bp.delete("/api/mcp/servers/<name>")
def mcp_server_delete(name):
    if not _may_change():
        return err("log in to change this", 401)
    gw = ctx().mcp
    raw = [x for x in (ctx().store.get_settings().get("_mcp_servers") or []) if x.get("name") != name]
    gw.save(raw)
    ctx().event("settings", f"MCP server {name} removed")
    return ok(servers=gw.view())


@bp.post("/api/mcp/servers/<name>/test")
def mcp_server_test(name):
    gw = ctx().mcp
    if not gw.server(name):
        return err("no such MCP server", 404)
    try:
        tools = gw.tools(name, fresh=True)
    except MCPError as exc:
        return err(f"{name}: {exc}", 502)
    return ok(tools=[{"name": t["name"], "description": (t.get("description") or "")[:300]} for t in tools],
              servers=gw.view())


@bp.get("/api/mcp/servers/<name>/tools")
def mcp_server_tools(name):
    """Every tool the server has, before the allow and deny lists, to pick from."""
    gw = ctx().mcp
    s = gw.server(name)
    if not s:
        return err("no such MCP server", 404)
    try:
        res = gw.client(s).request("tools/list", {}, 60)
    except MCPError as exc:
        return err(f"{name}: {exc}", 502)
    return ok(tools=[{"name": t.get("name"), "description": (t.get("description") or "")[:300]}
                     for t in res.get("tools") or [] if isinstance(t, dict)])
