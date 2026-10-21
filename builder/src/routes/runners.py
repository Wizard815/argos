"""Servers -> Local: install, configure and check the runners that live inside Argos (Mode 1)."""
from __future__ import annotations

import os
import re
import subprocess
import time
import urllib.parse
import urllib.request

from flask import Blueprint

from ..integrations import engine_api
from ..runners import ADDONS, DEVICES, TYPES, install_steps, rocm_archs, scan, torch_builds
from ..web import body, ctx, err, ok

bp = Blueprint("runners", __name__)
EDITABLE = ("device", "enabled", "label", "folder")
OPTS = ("repo", "torch_index", "gpu_target", "default_model", "program", "install", "start", "check", "task", "alias", "vram",
        "models_glob", "model_args")


def _rec(rid: str) -> tuple[dict, dict]:
    rec = ctx().runners.records()
    if rid not in rec:
        raise KeyError(rid)
    return rec, rec[rid]


@bp.get("/api/runners")
def runners_list():
    return ok(**ctx().runners.view())


@bp.get("/api/runners/torch-builds")
def runners_torch_builds():
    """Custom PyTorch builds Argos knows of, and the GPU architectures this machine has, for the picker."""
    return ok(builds=torch_builds(), archs=list(rocm_archs()))


@bp.put("/api/runners/<rid>")
def runners_update(rid):
    c = ctx()
    try:
        rec, r = _rec(rid)
    except KeyError:
        return err("no such runner", 404)
    d = body()
    for k in EDITABLE:
        if k in d:
            r[k] = d[k]
    if r.get("device") not in DEVICES:
        return err(f"device must be one of {', '.join(DEVICES)}")
    r["opts"] = {**(r.get("opts") or {}), **{k: d["opts"][k] for k in OPTS if k in (d.get("opts") or {})}}
    c.runners.save(rec)
    c.runners.apply()
    return ok(**c.runners.view())


@bp.post("/api/runners/custom")
def runners_custom():
    """A new custom runner from a git URL: cloned, then scanned for its install and start commands."""
    c, d = ctx(), body()
    repo = str(d.get("repo") or "").strip()
    if not re.match(r"^(https://|git@)[\w.@:/~+-]+$", repo):
        return err("give an https:// or git@ repository URL")
    name = re.sub(r"[^a-z0-9-]+", "-", (d.get("name") or repo.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git")).lower()).strip("-")[:40] or "custom"
    rec = c.runners.records()
    rid = name if name not in rec else f"{name}-{int(time.time()) % 10000}"
    folder = os.path.join(c.runners.root(), rid)
    try:
        os.makedirs(c.runners.root(), exist_ok=True)
        p = subprocess.run(["git", "clone", "--depth", "1", repo, folder], capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.SubprocessError) as exc:
        return err(f"could not run git: {exc}", 500)
    if p.returncode != 0:
        return err(f"git clone failed: {(p.stderr or p.stdout).strip()[-300:]}", 502)
    found = scan(folder)
    rec[rid] = {"id": rid, "kind": "custom", "device": "cpu", "enabled": True, "addon": False, "label": name,
                "opts": {"repo": repo, "task": found["task"],
                         "install": found["install"][0]["cmd"] if found["install"] else "",
                         "start": "", "check": "/"}, "scan": found}
    c.runners.save(rec)
    c.event("runners", f"cloned {repo} as runner {rid}", data={"runner": rid})
    return ok(runner=rid, scan=found, **c.runners.view())


@bp.post("/api/runners/<rid>/scan")
def runners_scan(rid):
    c = ctx()
    try:
        rec, r = _rec(rid)
    except KeyError:
        return err("no such runner", 404)
    found = scan(c.runners.folder(r))
    r["scan"] = found
    c.runners.save(rec)
    return ok(scan=found)


@bp.post("/api/runners/<rid>/install")
def runners_install(rid):
    c = ctx()
    try:
        rec, r = _rec(rid)
    except KeyError:
        return err("no such runner", 404)
    folder = c.runners.folder(r)
    steps = install_steps(r["kind"], folder, r.get("device", "cpu"), r.get("opts") or {})
    if not steps:
        return err("nothing to install: give an install command first")

    def done(success: bool, error: str) -> None:
        c.event("runners", f"{'installed' if success else 'could not install'} {rid}" + (f": {error}" if error else ""),
                level="info" if success else "error", data={"runner": rid})
        try:
            c.runners.apply()
        except Exception:  # noqa: BLE001 - the next tick writes it
            pass

    try:
        job = c.runners.installer.start(rid, folder, steps, done)
    except RuntimeError as exc:
        return err(str(exc), 409)
    c.event("runners", f"installing {rid} ({r.get('device', 'cpu')})", data={"runner": rid, "steps": len(steps)})
    return ok(job=job)


@bp.get("/api/runners/<rid>/install")
def runners_install_status(rid):
    return ok(job=ctx().runners.installer.status(rid))


@bp.delete("/api/runners/<rid>")
def runners_delete(rid):
    """Forget a custom runner. Its folder stays on disk: deleting files is left to you."""
    c = ctx()
    rec = c.runners.records()
    r = rec.get(rid)
    if not r:
        return err("no such runner", 404)
    if r["kind"] != "custom":
        return err("built-in runners can be turned off, not removed", 409)
    rec.pop(rid)
    c.runners.save(rec)
    c.runners.apply()
    return ok(folder=c.runners.folder(r))


@bp.put("/api/runners/overrides/<path:model_id>")
def runners_override(model_id):
    """Which runner serves one model file: a runner id, "none", or "" to go back to the automatic choice."""
    c = ctx()
    runner = str(body().get("runner") or "")
    if runner and runner != "none" and runner not in c.runners.records():
        return err("no such runner", 404)
    o = c.store.get_settings().get("_runner_overrides") or {}
    if runner:
        o[model_id] = runner
    else:
        o.pop(model_id, None)
    c.store.set_setting("_runner_overrides", o)
    c.runners.apply()
    return ok(overrides=o)


@bp.post("/api/runners/apply")
def runners_apply():
    c = ctx()
    changed = c.runners.apply()
    return ok(changed=changed, path=c.runners.engine_path(), models=sorted(c.runners.entries()))


@bp.post("/api/runners/<rid>/check")
def runners_check(rid):
    """Load this runner's first model through the engine and report whether it answered."""
    c = ctx()
    url = c.settings.get("integrations")["engine_url"]
    if not url:
        return err("the engine is not running", 409)
    models = [m for m in c.runners.served() if m["runner"] == rid]
    if not models:
        return err("this runner serves no model yet: install it, or add a model file it can serve", 409)
    mid = models[0]["id"]
    c.runners.apply()
    deadline, t0 = time.time() + 15, time.time()
    while time.time() < deadline and mid not in engine_api.model_ids(url):
        time.sleep(1)  # the engine reloads its config every couple of seconds
    check = (c.runners.entries().get(mid) or {}).get("checkEndpoint") or "/"
    try:
        # through /upstream so the engine starts it, at the runner's own health path (whisper-server has no /health)
        urllib.request.urlopen(url.rstrip("/") + f"/upstream/{urllib.parse.quote(mid, safe='')}{check}", timeout=300).read()  # noqa: S310
    except Exception as exc:  # noqa: BLE001 - reported to the page
        return err(f"{mid} did not start: {exc}", 502)
    return ok(model=mid, seconds=round(time.time() - t0, 1))


__all__ = ["bp", "TYPES", "ADDONS"]
