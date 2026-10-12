"""Workspace: sticky notes, panel layout, the models-folder file browser, extra-file downloads,
and changing a profile safely (see the changes, confirm, reload just that model in llama-swap)."""
from __future__ import annotations

import os
import threading
import time

from flask import Blueprint, request

from ..command import parse_command
from ..cookbook import variant_id
from ..downloader import DownloadError
from ..integrations import llama_swap
from ..library import scan_models
from ..web import body, ctx, err, ok
from . import core

bp = Blueprint("workspace", __name__)
FILE_TYPES = (".gguf", ".bin", ".safetensors")
LAYOUT_KEY = "_layout"


# ----- notes ----------------------------------------------------------------------------------
@bp.get("/api/notes")
def notes_list():
    return ok(notes=ctx().store.list_notes(request.args.get("view") or None))


@bp.post("/api/notes")
def notes_add():
    d = body()
    view = str(d.get("view") or "").strip()
    if not view:
        return err("view is required")
    return ok(note=ctx().store.add_note(view, str(d.get("title") or ""), str(d.get("body") or ""), str(d.get("color") or "yellow")))


@bp.put("/api/notes/<int:note_id>")
def notes_update(note_id):
    note = ctx().store.update_note(note_id, **{k: v for k, v in body().items() if k in ("title", "body", "color", "view", "x", "y", "w", "h")})
    return ok(note=note) if note else err("not found", 404)


@bp.get("/api/notes/<int:note_id>/history")
def notes_history(note_id):
    if not ctx().store.get_note(note_id):
        return err("not found", 404)
    return ok(versions=ctx().store.note_versions(note_id))


@bp.post("/api/notes/<int:note_id>/restore/<int:version_id>")
def notes_restore(note_id, version_id):
    """Bring back an old version. It becomes the newest one; nothing is lost."""
    c = ctx()
    v = next((x for x in c.store.note_versions(note_id) if x["id"] == version_id), None)
    if not v:
        return err("not found", 404)
    c.store.NOTE_MERGE_S, merge = 0, c.store.NOTE_MERGE_S  # a restore is always its own version
    try:
        note = c.store.update_note(note_id, title=v["title"], body=v["body"])
    finally:
        c.store.NOTE_MERGE_S = merge
    return ok(note=note)


@bp.delete("/api/notes/<int:note_id>")
def notes_delete(note_id):
    return ok() if ctx().store.delete_note(note_id) else err("not found", 404)


# ----- panel layout ----------------------------------------------------------------------------
@bp.get("/api/layout")
def layout_get():
    return ok(layout=ctx().store.get_settings().get(LAYOUT_KEY) or {})


@bp.put("/api/layout")
def layout_put():
    """{key: [panel ids in order]} for one or more containers; other containers keep their order."""
    d = body()
    if not all(isinstance(k, str) and isinstance(v, list) and all(isinstance(x, str) for x in v) for k, v in d.items()):
        return err("layout must map a container key to a list of panel ids")
    layout = ctx().store.get_settings().get(LAYOUT_KEY) or {}
    layout.update({k[:120]: [x[:120] for x in v][:100] for k, v in d.items()})
    ctx().store.set_setting(LAYOUT_KEY, layout)
    return ok(layout=layout)


# ----- the models folder ------------------------------------------------------------------------
def _root() -> str:
    return os.path.realpath(ctx().settings.get("paths")["models_dir"])


def _inside(root: str, rel: str) -> str:
    path = os.path.realpath(os.path.join(root, rel or ""))
    if path != root and not path.startswith(root + os.sep):
        raise ValueError("outside the models folder")
    return path


@bp.get("/api/files")
def files_tree():
    """One folder of the models folder: subfolders and model files, with sizes."""
    root = _root()
    try:
        path = _inside(root, request.args.get("dir") or "")
        names = sorted(os.listdir(path), key=str.lower)
    except ValueError as exc:
        return err(str(exc))
    except OSError:
        return err("that folder does not exist", 404)
    entries = []
    for n in names:
        if n.startswith("."):
            continue
        full = os.path.join(path, n)
        if os.path.isdir(full):
            entries.append({"name": n, "path": os.path.relpath(full, root), "abs": full, "dir": True})
        elif n.lower().endswith(FILE_TYPES):
            entries.append({"name": n, "path": os.path.relpath(full, root), "abs": full, "dir": False,
                            "size": os.path.getsize(full)})
    rel = os.path.relpath(path, root)
    return ok(root=root, dir="" if rel == "." else rel, parent=None if path == root else os.path.relpath(os.path.dirname(path), root),
              entries=sorted(entries, key=lambda e: (not e["dir"], e["name"].lower())))


@bp.get("/api/files/search")
def files_search():
    q = (request.args.get("q") or "").strip().lower()
    if len(q) < 2:
        return err("type at least two characters")
    root, hits = _root(), []
    for d, dirs, files in os.walk(root, followlinks=True):
        dirs[:] = [x for x in dirs if not x.startswith(".")]
        for f in files:
            if f.lower().endswith(FILE_TYPES) and q in f.lower():
                full = os.path.join(d, f)
                hits.append({"name": f, "path": os.path.relpath(full, root), "abs": full, "dir": False, "size": os.path.getsize(full)})
                if len(hits) >= 200:
                    return ok(results=hits, truncated=True)
    return ok(results=hits, truncated=False)


@bp.post("/api/catalog/download")
def catalog_download():
    """Download a file from Hugging Face into the model's own folder and register it as an extra file."""
    c, d = ctx(), body()
    kind, base, repo, name = d.get("kind"), (d.get("base_model") or "").strip(), (d.get("repo") or "").strip(), (d.get("file") or "").strip()
    if kind not in core.CATALOG_KINDS or not base or not repo or not name:
        return err("kind, base_model, repo and file are required")
    model = next((m for m in scan_models(c.settings.get("paths")["models_dir"]) if base in (m["id"], m["name"])), None)
    subdir = model["dir"] if model and model["dir"] != "." else repo.split("/")[-1]
    try:
        info = c.downloads.client().repo(repo)
        f = next((x for x in info["files"] if x["name"] == name), None)
        if not f:
            return err(f"{name} is not in {repo}")
        job = c.downloads.start(info["id"], [f], subdir=subdir)
    except DownloadError as exc:
        return err(str(exc), 502)
    path = os.path.join(job["dest"], name)
    fid = c.store.add_file(kind, base, name=os.path.basename(name), hf_repo=repo, file_path=path, notes=f"downloading ({job['id']})")
    return ok(id=fid, download=job, file_path=path)


# ----- changing a profile ----------------------------------------------------------------------
def diff_commands(before: str, after: str) -> list[dict]:
    """What changed between two commands, flag by flag. Spelling (-c vs --ctx-size) does not count."""
    def canon(cmd: str) -> dict[str, tuple[str, str]]:
        try:
            p = parse_command(cmd or "")
        except ValueError:
            return {"(command)": ("command", cmd)}
        out = {f"env:{k}": (k, v) for k, v in p.env.items()}
        for a in p.args:
            if a.name:
                out[a.name] = (a.flag or a.name, "" if a.value is None else str(a.value))
        out["(program)"] = ("program", p.program)
        return out
    b, a = canon(before), canon(after)
    changes = []
    for key in list(dict.fromkeys(list(b) + list(a))):
        if key in b and key not in a:
            changes.append({"name": b[key][0], "before": b[key][1], "after": None, "change": "removed", "env": key.startswith("env:")})
        elif key in a and key not in b:
            changes.append({"name": a[key][0], "before": None, "after": a[key][1], "change": "added", "env": key.startswith("env:")})
        elif b[key][1] != a[key][1]:
            changes.append({"name": a[key][0], "before": b[key][1], "after": a[key][1], "change": "changed", "env": key.startswith("env:")})
    return changes


def _variant_for_profile(profile_id: str) -> str:
    for p in core.cookbook_state()["presets"]:
        if (p.get("fields") or {}).get("profile_id") == profile_id:
            return variant_id(p)
    return ""


@bp.post("/api/profiles/diff")
def profiles_diff():
    """Before saving: what would change, which llama-swap model it is, and whether that model is loaded right now."""
    c, d = ctx(), body()
    new = d.get("launch_command") or ""
    old = c.store.get_profile(d["profile_id"]) if d.get("profile_id") else None
    changes = diff_commands(old["launch_command"] if old else "", new)
    if old and (d.get("name") or old["name"]) != old["name"]:
        changes.insert(0, {"name": "profile name", "before": old["name"], "after": d.get("name"), "change": "changed", "env": False})
    variant = _variant_for_profile(old["profile_id"]) if old else ""
    running = None
    url = c.settings.get("integrations")["llama_swap_url"]
    if url and variant:
        try:
            running = any(m.get("model") == variant for m in llama_swap.running(url))
        except Exception:  # noqa: BLE001 - unknown, not "not running"
            running = None
    return ok(changes=changes, existing=bool(old), variant_id=variant, running=running, llama_swap=bool(url))


@bp.post("/api/profiles/<profile_id>/apply")
def profiles_apply(profile_id):
    """After saving: hand the new command to llama-swap and, if asked, load that one model.

    llama-swap restarts only the model whose command changed; every other model stays loaded.
    """
    c, d = ctx(), body()
    if not c.store.get_profile(profile_id):
        return err("not found", 404)
    core.write_export()
    variant = _variant_for_profile(profile_id)
    url = c.settings.get("integrations")["llama_swap_url"]
    steps = {"variant_id": variant, "exported": True, "refresh": "skipped", "load": "skipped"}
    if not url:
        steps["refresh"] = "no llama-swap URL set"
        return ok(**steps)
    try:
        llama_swap.odysseus_refresh(url)
        steps["refresh"] = "ok"
    except Exception as exc:  # noqa: BLE001 - llama-swap may read the export file instead (stateFile)
        steps["refresh"] = f"llama-swap did not refresh: {exc}"
    if d.get("load") and variant:
        def load():
            time.sleep(float(d.get("delay_s", 3)))  # let llama-swap pick up the new config first
            try:
                llama_swap.load_model(url, variant)
                c.event("profile", f"loaded {variant} in llama-swap with the new settings", data={"model": variant})
            except Exception as exc:  # noqa: BLE001
                c.event("profile", f"could not load {variant} in llama-swap: {exc}", level="warn", data={"model": variant})
        threading.Thread(target=load, daemon=True).start()
        steps["load"] = "started"
    c.event("profile", f"applied profile changes to llama-swap ({variant or 'no variant yet'})", data=steps)
    return ok(**steps)


__all__ = ["bp", "diff_commands"]
