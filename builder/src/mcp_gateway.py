"""MCP gateway: your MCP servers behind one Argos address, or each on its own.

Settings -> MCP lists the servers: a command Argos runs (stdio) or a URL (streamable HTTP), with optional headers
and tool allow/deny lists. Argos then serves:

- ``/mcp``: every server at once (the all-in-one link). Tools are named ``<server>__<tool>``. ``?only=a,b`` or
  ``?skip=c`` narrows it for one app.
- ``/mcp/<server>``: one server alone, tools under their own names (a single link).

An API key decides which servers it reaches (all, only some, all but some, none: Settings -> Security). The
endpoint is stateless JSON-RPC over HTTP POST, the streamable HTTP transport without a session.

The same tools serve ``/v1/chat/completions`` requests that ask for them (mcp_chat.py): Argos runs the tool calls.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import subprocess
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from typing import Any, Optional

SERVERS_KEY = "_mcp_servers"
SEP = "__"
NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
PROTOCOL = "2025-06-18"
SERVED_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
TOOLS_TTL = 60
CALL_TIMEOUT = 120


class MCPError(Exception):
    pass


def clean_server(d: dict) -> dict:
    name = str(d.get("name") or "").strip().lower()
    if not NAME.match(name) or SEP in name:
        raise ValueError("the name is 1-40 lowercase letters, digits, - or _ (no __), e.g. github")
    transport = str(d.get("transport") or ("stdio" if d.get("command") else "http"))
    if transport not in ("http", "stdio"):
        raise ValueError("transport is http or stdio")
    out = {"name": name, "transport": transport, "enabled": d.get("enabled", True) is not False,
           "description": str(d.get("description") or "")[:200]}
    if transport == "http":
        url = str(d.get("url") or "").strip()
        if not url.startswith(("http://", "https://")):
            raise ValueError("the URL must start with http:// or https://")
        out["url"] = url
        headers = d.get("headers") or {}
        if isinstance(headers, str):
            headers = dict(line.split(":", 1) for line in headers.splitlines() if ":" in line)
        out["headers"] = {str(k).strip(): str(v).strip() for k, v in headers.items() if str(k).strip()}
    else:
        cmd = d.get("command") or ""
        args = d.get("args")
        if args is None and isinstance(cmd, str):
            parts = shlex.split(cmd)
            cmd, args = (parts[0], parts[1:]) if parts else ("", [])
        if not cmd:
            raise ValueError("give the command that starts it, e.g. npx -y @modelcontextprotocol/server-filesystem /data")
        out["command"] = str(cmd)
        out["args"] = [str(a) for a in (args or [])]
        env = d.get("env") or {}
        if isinstance(env, str):
            env = dict(line.split("=", 1) for line in env.splitlines() if "=" in line)
        out["env"] = {str(k).strip(): str(v) for k, v in env.items() if str(k).strip()}
    for k in ("allowed_tools", "disabled_tools"):
        v = d.get(k) or []
        if isinstance(v, str):
            v = v.replace("\n", ",").split(",")
        out[k] = [str(x).strip() for x in v if str(x).strip()]
    return out


def public_server(s: dict) -> dict:
    """A server as the page sees it: header and env values are hidden (they hold tokens)."""
    out = dict(s)
    if "headers" in out:
        out["headers"] = {k: "••••" for k in out["headers"]}
    if "env" in out:
        out["env"] = {k: "••••" for k in out["env"]}
    return out


def _expand(value: str) -> str:
    """${VAR} from Argos's environment, so a token can live in .env instead of the database."""
    return re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", lambda m: os.environ.get(m.group(1), ""), value)


# ----- clients ----------------------------------------------------------------------------------------------------
class HttpClient:
    """Streamable HTTP: JSON-RPC over POST; the answer is JSON or an event stream."""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.session = ""
        self.ready = False
        self.lock = threading.Lock()
        self.next_id = 0

    def _post(self, msg: dict, timeout: float) -> Optional[dict]:
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
                   **{k: _expand(v) for k, v in (self.cfg.get("headers") or {}).items()}}
        if self.session:
            headers["Mcp-Session-Id"] = self.session
        if self.ready:
            headers["MCP-Protocol-Version"] = PROTOCOL
        req = urllib.request.Request(self.cfg["url"], data=json.dumps(msg).encode(), headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - a URL the user set
                sid = resp.headers.get("Mcp-Session-Id")
                if sid:
                    self.session = sid
                if "id" not in msg:
                    return None
                ctype = resp.headers.get("Content-Type") or ""
                if "text/event-stream" in ctype:
                    return _from_events(resp, msg["id"])
                raw = resp.read()
                if not raw.strip():
                    return None
                data = json.loads(raw)
                if isinstance(data, list):
                    data = next((x for x in data if x.get("id") == msg["id"]), None)
                return data
        except urllib.error.HTTPError as exc:
            if exc.code == 404 and self.session:
                raise _SessionGone() from exc
            detail = exc.read()[:300].decode("utf-8", "replace")
            raise MCPError(f"HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise MCPError(str(getattr(exc, "reason", exc))) from exc

    def _init(self) -> None:
        self.session, self.ready = "", False
        self.next_id += 1
        got = self._post({"jsonrpc": "2.0", "id": self.next_id, "method": "initialize",
                          "params": {"protocolVersion": PROTOCOL, "capabilities": {},
                                     "clientInfo": {"name": "argos", "version": "1"}}}, 30)
        if not got or "error" in got:
            raise MCPError(f"initialize failed: {(got or {}).get('error')}")
        self.ready = True
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"}, 15)

    def request(self, method: str, params: Optional[dict] = None, timeout: float = CALL_TIMEOUT) -> dict:
        with self.lock:
            for attempt in (0, 1):
                if not self.ready:
                    self._init()
                self.next_id += 1
                try:
                    got = self._post({"jsonrpc": "2.0", "id": self.next_id, "method": method, "params": params or {}}, timeout)
                except _SessionGone:
                    self.ready = False
                    if attempt:
                        raise MCPError("the server dropped the session twice") from None
                    continue
                if not got:
                    raise MCPError("no answer")
                if "error" in got:
                    raise MCPError(str(got["error"].get("message") if isinstance(got["error"], dict) else got["error"]))
                return got.get("result") or {}
        raise MCPError("no answer")

    def close(self) -> None:
        pass


class _SessionGone(Exception):
    pass


def _from_events(resp, want_id) -> Optional[dict]:
    """The JSON-RPC answer with ``want_id`` from an event stream (other events are notifications)."""
    data_lines: list[str] = []
    for raw in resp:
        line = raw.decode("utf-8", "replace").rstrip("\r\n")
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
            continue
        if line == "" and data_lines:
            try:
                msg = json.loads("\n".join(data_lines))
            except ValueError:
                msg = None
            data_lines = []
            if isinstance(msg, dict) and msg.get("id") == want_id and ("result" in msg or "error" in msg):
                return msg
    return None


class StdioClient:
    """A server Argos starts: JSON-RPC, one message a line, on its stdin and stdout."""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.proc: Optional[subprocess.Popen] = None
        self.lock = threading.Lock()
        self.cond = threading.Condition()
        self.answers: dict[int, dict] = {}
        self.next_id = 0
        self.stderr: deque = deque(maxlen=50)
        self.ready = False

    def _start(self) -> None:
        env = {**os.environ, **{k: _expand(v) for k, v in (self.cfg.get("env") or {}).items()}}
        try:
            self.proc = subprocess.Popen([self.cfg["command"], *self.cfg.get("args", [])], stdin=subprocess.PIPE,  # noqa: S603
                                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, bufsize=0)
        except OSError as exc:
            raise MCPError(f"could not start {self.cfg['command']}: {exc}") from exc
        threading.Thread(target=self._read, args=(self.proc,), daemon=True, name=f"mcp-{self.cfg['name']}").start()
        threading.Thread(target=self._errs, args=(self.proc,), daemon=True).start()
        self.ready = False
        got = self._send("initialize", {"protocolVersion": PROTOCOL, "capabilities": {},
                                        "clientInfo": {"name": "argos", "version": "1"}}, 60)
        if "error" in got:
            raise MCPError(f"initialize failed: {got['error']}")
        self._write({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.ready = True

    def _errs(self, proc) -> None:
        for line in proc.stderr:
            self.stderr.append(line.decode("utf-8", "replace").rstrip())

    def _read(self, proc) -> None:
        for line in proc.stdout:
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if not isinstance(msg, dict):
                continue
            if "method" in msg and "id" in msg:  # the server asks something (ping, roots): answer plainly
                self._write({"jsonrpc": "2.0", "id": msg["id"], "result": {}} if msg["method"] == "ping" else
                            {"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32601, "message": "not supported"}})
                continue
            if "id" in msg:
                with self.cond:
                    self.answers[msg["id"]] = msg
                    self.cond.notify_all()
        with self.cond:
            self.cond.notify_all()

    def _write(self, msg: dict) -> None:
        try:
            self.proc.stdin.write((json.dumps(msg) + "\n").encode())
            self.proc.stdin.flush()
        except (OSError, AttributeError) as exc:
            raise MCPError(f"the server is not running: {exc}") from exc

    def _send(self, method: str, params: dict, timeout: float) -> dict:
        self.next_id += 1
        mid = self.next_id
        self._write({"jsonrpc": "2.0", "id": mid, "method": method, "params": params})
        deadline = time.time() + timeout
        with self.cond:
            while mid not in self.answers:
                left = deadline - time.time()
                if left <= 0:
                    raise MCPError(f"no answer to {method} in {timeout:g}s")
                if self.proc.poll() is not None:
                    raise MCPError("the server stopped: " + " | ".join(list(self.stderr)[-3:]))
                self.cond.wait(min(left, 1.0))
            return self.answers.pop(mid)

    def request(self, method: str, params: Optional[dict] = None, timeout: float = CALL_TIMEOUT) -> dict:
        with self.lock:
            if self.proc is None or self.proc.poll() is not None:
                self._start()
            got = self._send(method, params or {}, timeout)
        if "error" in got:
            e = got["error"]
            raise MCPError(str(e.get("message") if isinstance(e, dict) else e))
        return got.get("result") or {}

    def close(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(5)
            except subprocess.TimeoutExpired:
                self.proc.kill()


# ----- the gateway ------------------------------------------------------------------------------------------------
class Gateway:
    def __init__(self, store):
        self.store = store
        self.lock = threading.Lock()
        self.clients: dict[str, tuple[str, Any]] = {}
        self.tool_cache: dict[str, tuple[float, list]] = {}
        self.status: dict[str, dict] = {}

    def servers(self) -> list[dict]:
        out = []
        for s in self.store.get_settings().get(SERVERS_KEY) or []:
            try:
                out.append(clean_server(s))
            except ValueError:
                continue
        return out

    def save(self, servers: list[dict]) -> None:
        self.store.set_setting(SERVERS_KEY, servers)
        with self.lock:
            self.tool_cache.clear()

    def server(self, name: str) -> Optional[dict]:
        return next((s for s in self.servers() if s["name"] == name), None)

    def client(self, s: dict):
        stamp = hashlib.sha256(json.dumps(s, sort_keys=True).encode()).hexdigest()
        with self.lock:
            have = self.clients.get(s["name"])
            if have and have[0] == stamp:
                return have[1]
            if have:
                have[1].close()
                self.tool_cache.pop(s["name"], None)
            c = HttpClient(s) if s["transport"] == "http" else StdioClient(s)
            self.clients[s["name"]] = (stamp, c)
            return c

    def tools(self, name: str, fresh: bool = False) -> list[dict]:
        """A server's tools after its allow and deny lists (cached for a minute)."""
        s = self.server(name)
        if not s or not s["enabled"]:
            return []
        hit = self.tool_cache.get(name)
        if hit and not fresh and time.time() - hit[0] < TOOLS_TTL:
            return hit[1]
        c = self.client(s)
        tools, cursor = [], None
        t0 = time.time()
        try:
            for _ in range(20):
                res = c.request("tools/list", {"cursor": cursor} if cursor else {}, 60)
                tools.extend(t for t in res.get("tools") or [] if isinstance(t, dict) and t.get("name"))
                cursor = res.get("nextCursor")
                if not cursor:
                    break
        except MCPError as exc:
            self.status[name] = {"ok": False, "error": str(exc), "at": time.time()}
            raise
        allow, deny = set(s.get("allowed_tools") or []), set(s.get("disabled_tools") or [])
        kept = [t for t in tools if (not allow or t["name"] in allow) and t["name"] not in deny]
        self.status[name] = {"ok": True, "tools": len(kept), "all_tools": len(tools), "at": time.time(),
                             "ms": round((time.time() - t0) * 1000)}
        self.tool_cache[name] = (time.time(), kept)
        return kept

    def reachable(self, rules: Optional[dict], only: Optional[set] = None, skip: Optional[set] = None) -> list[str]:
        from .gateway import mcp_allowed
        return [s["name"] for s in self.servers() if s["enabled"] and mcp_allowed(s["name"], rules)
                and (not only or s["name"] in only) and s["name"] not in (skip or set())]

    def all_tools(self, names: list[str], prefixed: bool = True) -> tuple[list[dict], dict[str, str]]:
        """Tools of these servers, and the error of each server that did not answer."""
        out, errors = [], {}
        for n in names:
            try:
                for t in self.tools(n):
                    out.append({**t, "name": f"{n}{SEP}{t['name']}"} if prefixed else t)
            except MCPError as exc:
                errors[n] = str(exc)
        return out, errors

    def call(self, server: str, tool: str, args: dict) -> dict:
        s = self.server(server)
        if not s or not s["enabled"]:
            raise MCPError(f"no MCP server {server}")
        allow, deny = set(s.get("allowed_tools") or []), set(s.get("disabled_tools") or [])
        if (allow and tool not in allow) or tool in deny:
            raise MCPError(f"the tool {tool} is turned off for {server}")
        return self.client(s).request("tools/call", {"name": tool, "arguments": args or {}})

    def split(self, name: str) -> tuple[str, str]:
        server, sep, tool = name.partition(SEP)
        if not sep:
            raise MCPError(f"{name} is not <server>{SEP}<tool>")
        return server, tool

    def view(self) -> list[dict]:
        return [{**public_server(s), "status": self.status.get(s["name"]) or {}} for s in self.servers()]

    def close(self) -> None:
        with self.lock:
            for _, c in self.clients.values():
                c.close()
            self.clients.clear()


def result_text(result: dict) -> str:
    """A tool result as text for a model: its text parts, else its structured content, else the JSON."""
    parts = []
    for c in result.get("content") or []:
        if isinstance(c, dict):
            if c.get("type") == "text":
                parts.append(str(c.get("text") or ""))
            elif c.get("type") == "resource" and isinstance(c.get("resource"), dict):
                parts.append(str(c["resource"].get("text") or c["resource"].get("uri") or ""))
            else:
                parts.append(f"[{c.get('type')} content]")
    if not parts and result.get("structuredContent") is not None:
        parts.append(json.dumps(result["structuredContent"]))
    text = "\n".join(p for p in parts if p) or json.dumps(result)
    if result.get("isError"):
        text = "ERROR: " + text
    return text[:100000]
