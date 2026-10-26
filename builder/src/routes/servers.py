"""Servers: Local plus SSH machines, each with its own key, a Check, and what it has installed."""
from __future__ import annotations

from flask import Blueprint

from ..integrations.odysseus_api import OdysseusError, as_list
from ..servers import LOCAL, ServerError, clean_server, from_odysseus
from ..web import body, ctx, err, ok
from .odysseus_hub import client

bp = Blueprint("servers", __name__)


def _public(server: dict) -> dict:
    """The server row for the UI: never the private key, only whether one exists."""
    s = ctx().servers
    return {**server, "has_key": server["kind"] != LOCAL and s.has_key(server["id"])}


@bp.get("/api/servers")
def servers_list():
    ctx().servers.ensure_local()
    return ok(servers=[_public(s) for s in ctx().store.list_servers()])


@bp.post("/api/servers")
def servers_add():
    c = ctx()
    try:
        d = clean_server(body())
    except ServerError as exc:
        return err(str(exc))
    if d["kind"] == LOCAL:
        return err("Local already exists")
    if c.store.server_by_name(d["name"]):
        return err(f"a server named {d['name']!r} already exists")
    d["source"] = "ui"
    server = c.store.save_server(d)
    c.event("server", f"added server {server['name']}", data={"server_id": server["id"]})
    return ok(server=_public(server))


@bp.put("/api/servers/<int:server_id>")
def servers_update(server_id):
    c = ctx()
    current = c.store.get_server(server_id)
    if not current:
        return err("not found", 404)
    d = body()
    d = {k: v for k, v in d.items() if k not in ("kind", "source")}  # what a server is, and who owns it, does not change here
    if current["kind"] == LOCAL:
        d.pop("name", None)
        d.pop("host", None)
        d.pop("port", None)
    try:
        clean = clean_server({**d, "kind": current["kind"]}, partial=True)
    except ServerError as exc:
        return err(str(exc))
    clash = clean.get("name") and c.store.server_by_name(clean["name"])
    if clash and clash["id"] != server_id:
        return err(f"a server named {clean['name']!r} already exists")
    clean.pop("kind", None)
    clean["source"] = "ui"  # an edit in the app takes the server over from any seed file
    return ok(server=_public(c.store.save_server(clean, server_id)))


@bp.delete("/api/servers/<int:server_id>")
def servers_delete(server_id):
    c = ctx()
    s = c.store.get_server(server_id)
    if not s:
        return err("not found", 404)
    if s["kind"] == LOCAL:
        return err("Local cannot be removed")
    c.store.delete_server(server_id)
    c.event("server", f"removed server {s['name']}")
    return ok()


@bp.post("/api/servers/<int:server_id>/default")
def servers_default(server_id):
    c = ctx()
    if not c.store.set_default_server(server_id):
        return err("not found", 404)
    # profiles now run on another server: the engine re-reads them so its variants follow
    url = c.settings.get("integrations").get("engine_url")
    if url:
        import threading
        from ..integrations import engine_api

        def refresh():
            try:
                engine_api.odysseus_refresh(url)
            except Exception:  # noqa: BLE001 - it refreshes on its own schedule anyway
                pass
        threading.Thread(target=refresh, name="engine-refresh", daemon=True).start()
    return ok()


@bp.get("/api/servers/<int:server_id>/key")
def servers_key(server_id):
    c = ctx()
    s = c.store.get_server(server_id)
    if not s:
        return err("not found", 404)
    return ok(has_key=c.servers.has_key(server_id), public_key=c.servers.public_key(server_id),
              command=c.servers.install_command(s))


@bp.post("/api/servers/<int:server_id>/key")
def servers_key_make(server_id):
    """Make this server's key pair (``force`` replaces an existing one) and return the install command."""
    c = ctx()
    s = c.store.get_server(server_id)
    if not s:
        return err("not found", 404)
    try:
        c.servers.make_key(s, force=bool(body().get("force")))
    except ServerError as exc:
        return err(str(exc))
    return ok(has_key=True, public_key=c.servers.public_key(server_id), command=c.servers.install_command(s))


@bp.post("/api/servers/<int:server_id>/check")
def servers_check(server_id):
    c = ctx()
    try:
        server = c.servers.check(server_id)
    except ServerError as exc:
        return err(str(exc), 404)
    if server["status"] == "failed":
        c.event("server", f"{server['name']}: {server['last_error']}", level="warn", data={"server_id": server_id})
    return ok(server=_public(server))


@bp.post("/api/servers/import/odysseus")
def servers_import_odysseus():
    """Copy the servers Odysseus' Cookbook knows. Argos makes its own key for each: Odysseus' keys stay in Odysseus."""
    c = ctx()
    try:
        raw = as_list(client().cookbook()["servers"], "servers")
    except OdysseusError as exc:
        return err(str(exc), 502)
    items, notes = from_odysseus(raw)
    res = c.servers.seed(items, source="odysseus")
    c.event("server", f"imported {len(res['created'])} server(s) from Odysseus", data=res)
    return ok(**res, notes=notes, servers=[_public(s) for s in c.store.list_servers()])


__all__ = ["bp"]
