"""Local model library, prefabs, hardware, Hugging Face search and downloads."""
from __future__ import annotations

import os

from flask import Blueprint, request

from ..downloader import DownloadError, fit
from ..library import library_with_profiles, prefab_command, scan_models
from ..telemetry import read_amd_sysfs, read_llama_swap, read_nvidia_smi
from ..web import body, check_id, ctx, err, ok
from .core import _render

bp = Blueprint("models", __name__)


def models_dir() -> str:
    return ctx().settings.get("paths")["models_dir"]


@bp.get("/api/library")
def library():
    c = ctx()
    d = models_dir()
    models = library_with_profiles(scan_models(d), c.store.list_profiles())
    return ok(models_dir=d, exists=os.path.isdir(d), models=models)


@bp.post("/api/library/prefab")
def prefab():
    """The starting command for one scanned model (by path), in Odysseus form."""
    d = body()
    path = d.get("path") or ""
    found = next((m for m in scan_models(models_dir()) if m["path"] == path), None)
    if not found:
        return err("that file is not in the models directory", 404)
    cmd = prefab_command(found, ctx().settings.get("prefab"))
    return ok(model=found, **_render(cmd, "odysseus"))


def hardware_info() -> dict:
    c = ctx()
    vram_setting = float(c.settings.get("downloads")["vram_gb"] or 0)
    gpus, source = c.store.gpu_latest(), "telemetry"
    if not gpus:
        url = c.settings.get("integrations")["llama_swap_url"]
        readers = ([("llama-swap", lambda: read_llama_swap(url))] if url else []) + \
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
        sizes = {f["name"]: f["size"] for f in info["files"]}
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
        sub = os.path.relpath(row["dest"], models_dir())
        job = c.downloads.start(row["repo"], row["files"], subdir=sub if not sub.startswith("..") else "")
    except DownloadError as exc:
        return err(str(exc))
    return ok(download=job)
