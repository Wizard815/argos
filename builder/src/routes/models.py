"""Local model library, prefabs, hardware, Hugging Face search and downloads."""
from __future__ import annotations

import os
import re

from flask import Blueprint, request

from ..downloader import DownloadError, fit
from ..integrations import engine_api
from ..library import forget_scans, helper_files, library_with_profiles, read_helpers_file, write_helpers_file, prefab_command, prefab_with_launcher, scan_media, scan_models, NAMES_KEY, with_names
from ..telemetry import read_amd_sysfs, read_engine, read_nvidia_smi
from ..web import body, check_id, ctx, err, ok
from .core import _render

bp = Blueprint("models", __name__)


def models_dir() -> str:
    return ctx().settings.get("paths")["models_dir"]


@bp.get("/api/library")
def library():
    c = ctx()
    d = models_dir()
    names = c.store.get_settings().get(NAMES_KEY) or {}
    models = with_names(library_with_profiles(scan_models(d), c.store.list_profiles()), names)
    return ok(models_dir=d, exists=os.path.isdir(d), models=models, media=with_names(scan_media(d), names))


@bp.get("/api/library/listed")
def library_listed():
    """The base models apps see in the model list anyway. Apps see profiles by default, not the bare models."""
    url = ctx().settings.get("integrations").get("engine_url")
    try:
        return ok(listed=engine_api.listed_models(url) if url else [], reachable=bool(url))
    except Exception as exc:  # noqa: BLE001 - shown as "unknown" in the list
        return ok(listed=[], reachable=False, error=str(exc))


@bp.put("/api/library/listed/<path:model_id>")
def library_set_listed(model_id):
    url = ctx().settings.get("integrations").get("engine_url")
    if not url:
        return err("no engine configured")
    on = bool(body().get("listed"))
    try:
        engine_api.set_model_listed(url, model_id, on)
    except Exception as exc:  # noqa: BLE001
        return err(f"the engine did not take it: {exc}", 502)
    ctx().event("settings", f"base model {model_id} {'shown to' if on else 'hidden from'} apps")
    return ok(listed=engine_api.listed_models(url))


@bp.put("/api/library/names/<path:model_id>")
def library_rename(model_id):
    """Show a model under another name, or under its file's name again with an empty one. No file is touched."""
    c = ctx()
    name = str(body().get("name") or "").strip()[:120]
    names = c.store.get_settings().get(NAMES_KEY) or {}
    if name:
        names[model_id] = name
    else:
        names.pop(model_id, None)
    c.store.set_setting(NAMES_KEY, names)
    return ok(names=names)


@bp.post("/api/library/rescan")
def library_rescan():
    """Rescan the models folder: the engine regenerates its model list, and the library is read again."""
    c = ctx()
    forget_scans()  # a rescan is a request to look at the disk again
    url = c.settings.get("integrations")["engine_url"]
    engine = None
    if url:
        try:
            engine = engine_api.rescan(url)
        except Exception as exc:  # noqa: BLE001 - the library still rescans
            engine = {"ok": False, "error": f"engine not reachable: {exc}"}
        if engine.get("ok"):
            c.event("library", f"rescanned the models folder: the engine lists {engine.get('models')} models"
                    + (" (its model list changed)" if engine.get("changed") else ""))
    return ok(engine=engine, models=len(scan_models(models_dir())))


EMBED = re.compile(r"(?i)embed|(?<![a-z])(bge|e5|gte)-|rerank")


@bp.get("/api/models/available")
def models_available():
    """Model ids to offer in dropdowns: what the engine lists (loaded or not), and the library's embedding models."""
    c = ctx()
    url = c.settings.get("integrations")["engine_url"]
    engine, error = [], ""
    if url:
        try:
            engine = sorted(engine_api.model_ids(url))
        except Exception as exc:  # noqa: BLE001 - the dropdown still works from the library
            error = str(exc)
    library = scan_models(models_dir())
    return ok(engine=engine, embeddings=[m for m in engine if EMBED.search(m)] or sorted({m["id"] for m in library if m.get("kind") == "embedding"}),
              library=sorted({m["id"] for m in library}), engine_error=error, engine_connected=bool(url and not error))


# ----- removing models, and your groups -----------------------------------------------------
def _find_library_entry(path: str):
    d = models_dir()
    for m in scan_models(d) + scan_media(d):
        if m["path"] == path:
            return m
    return None


def _files_of(entry: dict) -> list[str]:
    """Every file that makes up a model: all parts of a split GGUF, or the whole folder of a diffusers model."""
    import glob
    path = entry["path"]
    if entry.get("format") == "diffusers":
        return [path]
    m = re.search(r"-(\d{5})-of-(\d{5})\.gguf$", path, re.IGNORECASE)
    if m:
        return sorted(glob.glob(glob.escape(path[: m.start()]) + "-*-of-" + m.group(2) + ".gguf"))
    return [path]


def _inside_models_dir(p: str) -> bool:
    root = os.path.realpath(models_dir())
    real = os.path.realpath(p)
    return real != root and real.startswith(root + os.sep)


@bp.post("/api/library/delete/check")
def library_delete_check():
    """What deleting a model would remove, and which profiles use it -- for the confirm question."""
    entry = _find_library_entry(str(body().get("path") or ""))
    if not entry:
        return err("that model is not in the models folder", 404)
    files = _files_of(entry)
    profiles = [p["name"] for p in ctx().store.list_profiles() if entry["id"] and p.get("model_id") == entry["id"]]
    return ok(model=entry["name"], files=files, size=entry["size"], profiles=profiles,
              mmproj=[p for p in entry.get("mmproj") or [] if _only_user(p, entry)])


def _only_user(mmproj: str, entry: dict) -> bool:
    """True when no other model in the folder pairs with this projector, so it can go with the model."""
    folder = os.path.dirname(mmproj)
    return not any(m["path"] != entry["path"] and os.path.dirname(m["path"]) == folder for m in scan_models(models_dir()))


@bp.post("/api/library/delete")
def library_delete():
    """Delete a model's file(s) from the models folder. Its profiles are kept (they just stop working)."""
    import shutil
    c, d = ctx(), body()
    entry = _find_library_entry(str(d.get("path") or ""))
    if not entry:
        return err("that model is not in the models folder", 404)
    files = _files_of(entry)
    if d.get("with_mmproj"):
        files += [p for p in entry.get("mmproj") or [] if _only_user(p, entry)]
    removed, problems = [], []
    for f in files:
        if not _inside_models_dir(f):
            problems.append(f"{f}: outside the models folder")
            continue
        try:
            shutil.rmtree(f) if os.path.isdir(f) else os.remove(f)
            removed.append(f)
        except OSError as exc:
            problems.append(f"{os.path.basename(f)}: {exc.strerror or exc}")
    forget_scans()
    c.event("library", f"deleted {entry['name']} ({len(removed)} file{'s' if len(removed) != 1 else ''})",
            level="warn" if problems else "info", data={"files": removed, "problems": problems})
    if problems and not removed:
        return err("; ".join(problems), 500)
    return ok(removed=removed, problems=problems)


GROUPS_KEY = "_model_groups"


@bp.get("/api/library/groups")
def library_groups():
    saved = ctx().store.get_settings().get(GROUPS_KEY)
    return ok(groups=(saved or {}).get("groups") or [])


@bp.put("/api/library/groups")
def library_groups_save():
    """Your own model groups: [{name, models: [model ids]}]. A model is in at most one group."""
    raw = body().get("groups")
    if not isinstance(raw, list):
        return err("groups must be a list")
    groups, taken = [], set()
    for g in raw[:200]:
        name = str((g or {}).get("name") or "").strip()[:80]
        if not name or any(x["name"].lower() == name.lower() for x in groups):
            continue
        models = []
        for m in (g.get("models") or [])[:2000]:
            m = str(m)[:300]
            if m and m not in taken:
                taken.add(m)
                models.append(m)
        groups.append({"name": name, "models": models})
    ctx().store.set_setting(GROUPS_KEY, {"groups": groups})
    return ok(groups=groups)


@bp.get("/api/library/helpers")
def library_helpers():
    """The files that can be added onto a model (vision projectors, MTP heads), relative to the models folder."""
    return ok(files=helper_files(ctx().settings.get("paths")["models_dir"]))


@bp.put("/api/library/helpers")
def library_helpers_set():
    """{path: the model file, mtp / mmproj: a helper's path relative to the models folder, "" for none, or null for
    automatic (one beside it, or named like it)}. Kept in the models folder's .argos-helpers.json."""
    d = body()
    root = ctx().settings.get("paths")["models_dir"]
    path = str(d.get("path") or "")
    rel = os.path.relpath(path, root) if os.path.isabs(path) else path
    if rel.startswith("..") or not os.path.isfile(os.path.join(root, rel)):
        return err("no such model file in the models folder", 404)
    data = read_helpers_file(root)
    entry = dict(data.get(rel) or {})
    for which in ("mtp", "mmproj"):
        if which not in d:
            continue
        v = d[which]
        if v is None:
            entry.pop(which, None)
        else:
            v = str(v)
            if v and (v.startswith("..") or os.path.isabs(v) or not os.path.isfile(os.path.join(root, v))):
                return err(f"{which}: no such file in the models folder: {v}", 404)
            entry[which] = v
    data[rel] = entry
    try:
        write_helpers_file(root, data)
    except OSError as exc:
        return err(f"could not save {root}/.argos-helpers.json: {exc}", 500)
    ctx().event("library", f"helpers of {rel}: " + ", ".join(f"{k} {v or 'none'}" for k, v in entry.items()) if entry else f"helpers of {rel}: automatic")
    m = next((x for x in scan_models(root) if x["path"] == os.path.join(root, rel)), None)
    return ok(model=m)


@bp.post("/api/library/prefab")
def prefab():
    """The starting command for one scanned model (by path), in Odysseus form."""
    d = body()
    path = d.get("path") or ""
    found = next((m for m in scan_models(models_dir()) if m["path"] == path), None)
    if not found:
        return err("that file is not in the models directory", 404)
    cmd = prefab_command(found, prefab_with_launcher(ctx().settings.get("prefab"),
                                                    ctx().servers.default_launcher()))
    return ok(model=found, **_render(cmd, "odysseus"))


def hardware_info() -> dict:
    c = ctx()
    vram_setting = float(c.settings.get("downloads")["vram_gb"] or 0)
    gpus, source = c.store.gpu_latest(), "telemetry"
    if not gpus:
        url = c.settings.get("integrations")["engine_url"]
        readers = ([("engine", lambda: read_engine(url))] if url else []) + \
                  [("sysfs", read_amd_sysfs), ("nvidia-smi", read_nvidia_smi)]
        for source, reader in readers:
            try:
                gpus = reader()
            except Exception:  # noqa: BLE001 - try the next source
                gpus = []
            if gpus:
                break
    total = sum((g.get("mem_total_mb") or 0) for g in gpus) / 1024
    return {"gpus": gpus, "source": source if gpus else "", "vram_gb": vram_setting or round(total, 1),
            "vram_from_settings": bool(vram_setting)}


@bp.get("/api/hardware")
def hardware():
    return ok(**hardware_info())


@bp.get("/api/hf/search")
def hf_search():
    q = (request.args.get("q") or "").strip()
    if len(q) < 2:
        return err("type at least two characters")
    try:
        return ok(results=ctx().downloads.client().search(q))
    except DownloadError as exc:
        return err(str(exc), 502)


@bp.get("/api/hf/repo")
def hf_repo():
    try:
        info = ctx().downloads.client().repo(request.args.get("id") or "")
    except DownloadError as exc:
        return err(str(exc), 502)
    vram = hardware_info()["vram_gb"]
    target = os.path.join(models_dir(), info["id"].split("/")[-1])
    for q in info["quants"]:
        q["fit"] = fit(q["size"], vram)
        q["downloaded"] = all(os.path.exists(os.path.join(target, f)) for f in q["files"])
    return ok(**info, vram_gb=vram, dest=target)


@bp.post("/api/downloads")
def downloads_start():
    """Body: {repo, files: [names]}; sizes come from the repo listing."""
    c, d = ctx(), body()
    names = d.get("files") or []
    if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
        return err("files must be a list of file names")
    try:
        info = c.downloads.client().repo(d.get("repo") or "")
        sizes = {f["name"]: f["size"] for f in info.get("all") or info["files"]}
        unknown = sorted(set(names) - set(sizes))
        if unknown:
            return err(f"not in that repo: {', '.join(unknown)}")
        job = c.downloads.start(info["id"], [{"name": n, "size": sizes[n]} for n in names])
    except DownloadError as exc:
        return err(str(exc), 502)
    return ok(download=job)


@bp.get("/api/downloads")
def downloads_list():
    return ok(downloads=ctx().downloads.list())


@bp.post("/api/downloads/<job_id>/cancel")
def downloads_cancel(job_id):
    return ok() if ctx().downloads.cancel(check_id(job_id)) else err("not running", 404)


@bp.post("/api/downloads/<job_id>/retry")
def downloads_retry(job_id):
    c = ctx()
    row = next((r for r in c.store.list_downloads() if r["id"] == check_id(job_id)), None)
    if not row:
        return err("not found", 404)
    if row["status"] in ("queued", "running"):
        return err("already running")
    try:
        try:
            sub = os.path.relpath(row["dest"], models_dir())
        except ValueError:  # another drive (Windows)
            sub = row["dest"]
        outside = sub.startswith("..") or os.path.isabs(sub)  # a runner's own folder: back to the same place
        job = c.downloads.start(row["repo"], row["files"], subdir="" if outside else sub, dest=row["dest"] if outside else "")
    except DownloadError as exc:
        return err(str(exc))
    return ok(download=job)
