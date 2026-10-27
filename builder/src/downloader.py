"""Hugging Face search and resumable GGUF downloads.

Search and repo listings use the Hub's JSON API; files come from
``<endpoint>/<repo>/resolve/main/<file>``. Downloads run in background
threads, write ``<file>.part`` and resume it with a Range request, and land
in ``<models_dir>/<repo name>/<file>`` -- a plain folder of .gguf files that
Argos's recursive modelscan picks up as-is. ``HF_ENDPOINT``
(Settings -> Downloads) points everything at a mirror.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable, Optional

from .db import Store, new_id
from .library import SHARD, forget_scans, is_mmproj, quant_of

REPO_ID = re.compile(r"^[A-Za-z0-9][\w.-]*/[\w.-]+$")
CHUNK = 1 << 20


class DownloadError(Exception):
    pass


def _safe_relpath(name: str) -> str:
    parts = [p for p in name.replace("\\", "/").split("/") if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts) or name.startswith("/"):
        raise DownloadError(f"refusing unsafe file name {name!r}")
    return "/".join(parts)


class HubClient:
    def __init__(self, endpoint: str, token: str = "", timeout: float = 20):
        self.endpoint = (endpoint or "https://huggingface.co").rstrip("/")
        self.token = token
        self.timeout = timeout

    def _request(self, url: str, headers: Optional[dict] = None) -> urllib.request.Request:
        req = urllib.request.Request(url, headers={"User-Agent": "Argos", **(headers or {})})
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        return req

    def _json(self, path: str, params: Optional[dict] = None):
        url = f"{self.endpoint}{path}" + (f"?{urllib.parse.urlencode(params)}" if params else "")
        try:
            with urllib.request.urlopen(self._request(url), timeout=self.timeout) as r:  # noqa: S310
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise DownloadError(f"{exc.code} from Hugging Face: gated or private repo, set an HF token") from exc
            if exc.code == 404:
                raise DownloadError("not found on Hugging Face") from exc
            raise DownloadError(f"Hugging Face returned HTTP {exc.code}") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise DownloadError(f"cannot reach {self.endpoint}: {getattr(exc, 'reason', exc)}") from exc

    def search(self, query: str, limit: int = 40) -> list[dict]:
        rows = self._json("/api/models", {"search": query, "filter": "gguf", "sort": "downloads",
                                          "direction": "-1", "limit": str(limit)})
        return [{"id": r.get("id") or r.get("modelId"), "downloads": r.get("downloads", 0),
                 "likes": r.get("likes", 0), "updated": r.get("lastModified") or r.get("createdAt"),
                 "gated": bool(r.get("gated"))} for r in rows if isinstance(r, dict)]

    def repo(self, repo_id: str) -> dict:
        if not REPO_ID.match(repo_id or ""):
            raise DownloadError("repo must look like org/name")
        info = self._json(f"/api/models/{repo_id}", {"blobs": "true"})
        every = [{"name": s.get("rfilename", ""), "size": s.get("size") or (s.get("lfs") or {}).get("size") or 0}
                 for s in info.get("siblings") or [] if s.get("rfilename")]
        files = [f for f in every if f["name"].lower().endswith(".gguf")]
        # "all" is every file, for the downloads that are not GGUF (a whisper ggml .bin, an image model, a voice)
        return {"id": info.get("id", repo_id), "gated": bool(info.get("gated")), "files": files, "all": every,
                "quants": group_quants(files), "mmproj": [f for f in files if is_mmproj(f["name"])]}

    def file_url(self, repo_id: str, name: str) -> str:
        return f"{self.endpoint}/{repo_id}/resolve/main/{urllib.parse.quote(name)}"


def group_quants(files: list[dict]) -> list[dict]:
    """One entry per quant; split GGUFs are one entry with all their shards."""
    groups: dict[str, dict] = {}
    for f in files:
        if is_mmproj(f["name"]):
            continue
        key = SHARD.sub(".gguf", f["name"])
        g = groups.setdefault(key, {"quant": quant_of(f["name"]) or os.path.basename(key),
                                    "name": os.path.basename(key), "files": [], "size": 0})
        g["files"].append(f["name"])
        g["size"] += f["size"] or 0
    for g in groups.values():
        g["files"].sort()
        g["shards"] = len(g["files"])
    return sorted(groups.values(), key=lambda g: g["size"])


def fit(size_bytes: int, vram_gb: float) -> str:
    """Rough fit: weights plus ~15% and 1 GB for KV cache and compute buffers."""
    if not vram_gb or not size_bytes:
        return "unknown"
    need = size_bytes / 1e9 * 1.15 + 1.0
    return "fits" if need <= vram_gb * 0.92 else ("tight" if need <= vram_gb else "too big")


class Downloads:
    """Background download jobs. Progress is in memory and mirrored to the DB."""

    def __init__(self, store: Store, settings: Callable[[], dict], on_event: Callable[..., None]):
        self.store = store
        self.settings = settings  # -> {"models_dir", "hf_endpoint", "hf_token"}
        self.on_event = on_event
        self._jobs: dict[str, dict] = {}
        self._cancel: dict[str, threading.Event] = {}
        self._lock = threading.Lock()
        for row in store.list_downloads():
            if row["status"] in ("queued", "running"):  # interrupted by a restart
                row.update(status="interrupted")
                store.save_download(row)

    def client(self) -> HubClient:
        s = self.settings()
        return HubClient(s["hf_endpoint"], s["hf_token"])

    def start(self, repo_id: str, files: list[dict], subdir: str = "", dest: str = "") -> dict:
        """``files`` = [{"name", "size"}] as returned by HubClient.repo(); a file may add "save_as", the path it
        is saved under instead of its name. ``dest`` is a folder outside the models folder (a runner's own,
        such as Kokoro's voices), chosen by Argos, never by a request."""
        if not REPO_ID.match(repo_id or ""):
            raise DownloadError("repo must look like org/name")
        if not files:
            raise DownloadError("choose at least one file")
        for f in files:
            _safe_relpath(f["name"])
            _safe_relpath(f.get("save_as") or f["name"])
        dest = dest or os.path.join(self.settings()["models_dir"], _safe_relpath(subdir or repo_id.split("/")[-1]))
        job = {"id": new_id("dl"), "repo": repo_id, "files": files, "dest": dest, "status": "queued",
               "bytes_done": 0, "bytes_total": sum(int(f.get("size") or 0) for f in files),
               "error": "", "created_at": time.time(), "speed": 0.0, "current": ""}
        with self._lock:
            self._jobs[job["id"]] = job
            self._cancel[job["id"]] = threading.Event()
        self.store.save_download(job)
        threading.Thread(target=self._run, args=(job,), daemon=True).start()
        return job

    def cancel(self, job_id: str) -> bool:
        ev = self._cancel.get(job_id)
        if not ev:
            return False
        ev.set()
        return True

    def list(self) -> list[dict]:
        live = {j["id"]: j for j in self._jobs.values()}
        rows = []
        for row in self.store.list_downloads():
            rows.append({**row, **{k: live[row["id"]][k] for k in ("speed", "current", "bytes_done", "status")}}
                        if row["id"] in live else row)
        return rows

    def _run(self, job: dict) -> None:
        client = self.client()
        cancel = self._cancel[job["id"]]
        job["status"] = "running"
        self.store.save_download(job)
        self.on_event("download", f"downloading {len(job['files'])} file(s) from {job['repo']}",
                      data={"id": job["id"], "dest": job["dest"]})
        done_before = 0
        try:
            for f in job["files"]:
                rel = _safe_relpath(f.get("save_as") or f["name"])
                job["current"] = rel
                target = os.path.join(job["dest"], rel)
                done_before += self._fetch(client, job, f, target, done_before, cancel)
            job["status"] = "done"
            forget_scans()
            self.on_event("download", f"finished {job['repo']} -> {job['dest']}", data={"id": job["id"]})
        except _Cancelled:
            job["status"] = "cancelled"
            self.on_event("download", f"cancelled {job['repo']} (partial files kept for resume)",
                          level="warn", data={"id": job["id"]})
        except Exception as exc:  # noqa: BLE001 - reported to the UI and the event log
            job.update(status="failed", error=str(exc))
            self.on_event("download", f"download of {job['repo']} failed: {exc}", level="error",
                          data={"id": job["id"]})
        job["speed"] = 0.0
        self.store.save_download(job)

    def _fetch(self, client: HubClient, job: dict, f: dict, target: str, done_before: int,
               cancel: threading.Event) -> int:
        expected = int(f.get("size") or 0)
        if expected and os.path.exists(target) and os.path.getsize(target) == expected:
            job["bytes_done"] = done_before + expected
            return expected
        os.makedirs(os.path.dirname(target), exist_ok=True)
        part = target + ".part"
        have = os.path.getsize(part) if os.path.exists(part) else 0
        headers = {"Range": f"bytes={have}-"} if have else {}
        req = client._request(client.file_url(job["repo"], f["name"]), headers)
        try:
            resp = urllib.request.urlopen(req, timeout=60)  # noqa: S310
        except urllib.error.HTTPError as exc:
            if exc.code == 416 and expected and have >= expected:  # .part already complete
                os.replace(part, target)
                forget_scans()
                return expected
            if exc.code in (401, 403):
                raise DownloadError(f"HTTP {exc.code}: gated or private, set an HF token") from exc
            raise DownloadError(f"HTTP {exc.code} for {f['name']}") from exc
        with resp:
            if have and resp.status != 206:
                have = 0  # server ignored the Range header: start over
            total = expected or (have + int(resp.headers.get("Content-Length") or 0))
            mode = "ab" if have else "wb"
            last_t, last_b = time.time(), have
            with open(part, mode) as out:
                written = have
                while True:
                    if cancel.is_set():
                        raise _Cancelled()
                    chunk = resp.read(CHUNK)
                    if not chunk:
                        break
                    out.write(chunk)
                    written += len(chunk)
                    job["bytes_done"] = done_before + written
                    now = time.time()
                    if now - last_t >= 1.0:
                        job["speed"] = (written - last_b) / (now - last_t)
                        last_t, last_b = now, written
                        self.store.save_download(job)
        if total and written != total:
            raise DownloadError(f"{f['name']}: got {written} of {total} bytes; retry to resume")
        os.replace(part, target)
        forget_scans()
        return written


class _Cancelled(Exception):
    pass


__all__ = ["HubClient", "Downloads", "DownloadError", "group_quants", "fit"]
