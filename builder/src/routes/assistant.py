"""The assistant chat: state, streaming turns, reset, and one-click setup."""
from __future__ import annotations

import json
import os
import re

from flask import Blueprint, Response, stream_with_context

from ..assistant import DEFAULT_QUANT, DEFAULT_REPOS, PROFILE_NAME, pick_quant
from ..downloader import DownloadError
from ..library import model_id_for_command, prefab_command, scan_models
from ..web import body, ctx, err, ok

bp = Blueprint("assistant", __name__)


@bp.get("/api/assistant")
def state():
    c = ctx()
    a = c.assistant
    return ok(messages=c.store.list_messages(), summary=a.summary(), tokens=a.context_tokens(),
              budget=a.budget(), server=a.server_state(), settings=c.settings.get("assistant"))


@bp.get("/api/assistant/digest")
def digest():
    return ok(digest=ctx().assistant.digest())


@bp.post("/api/assistant/chat")
def chat():
    text = str(body().get("message") or "")
    assistant = ctx().assistant

    def events():
        for ev in assistant.chat(text):
            yield f"data: {json.dumps(ev)}\n\n"

    return Response(stream_with_context(events()), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@bp.post("/api/assistant/ask")
def ask():
    """A one-off question about some log lines (the Events view sends the ones on screen)."""
    d = body()
    question = str(d.get("question") or "").strip()
    if not question:
        return err("ask a question")
    lines = [str(x)[:300] for x in (d.get("lines") or [])][:60]
    try:
        answer = ctx().assistant.ask(question, "\n".join(lines), str(d.get("title") or "Log lines"))
    except Exception as exc:  # noqa: BLE001 - the assistant may not be set up
        return err(f"the assistant could not answer: {exc}", 502)
    return ok(answer=answer)


@bp.get("/api/assistant/memory")
def memory_status():
    return ok(**ctx().memory.status())


@bp.post("/api/assistant/memory/search")
def memory_search():
    d = body()
    q = str(d.get("q") or "").strip()
    if not q:
        return err("type something to search for")
    sources = set(d.get("sources") or []) or None
    return ok(results=ctx().memory.search(q, max(1, min(int(d.get("k") or 10), 50)), sources))


@bp.post("/api/assistant/memory/sync")
def memory_sync():
    return ok(result=ctx().memory.sync(budget=max(1, min(int(body().get("budget") or 256), 5000))), **ctx().memory.status())


@bp.post("/api/assistant/memory/rebuild")
def memory_rebuild():
    c = ctx()
    c.memory.rebuild()
    c.event("memory", "cleared the assistant's memory vectors; they are rebuilt in the background")
    return ok(**c.memory.status())


@bp.post("/api/assistant/reset")
def reset():
    ctx().assistant.reset()
    return ok()


@bp.post("/api/assistant/stop")
def stop():
    ctx().assistant.stop()
    return ok()


def _local_qwen06(models_dir: str):
    found = [m for m in scan_models(models_dir) if re.search(r"qwen3-0\.6b", m["name"], re.IGNORECASE)
             and m["kind"] == "chat"]
    found.sort(key=lambda m: (m["quant"] != DEFAULT_QUANT, m["size"]))
    return found[0] if found else None


@bp.post("/api/assistant/setup")
def setup():
    """Point the assistant at Qwen3-0.6B: reuse a local file, or download the Q8_0."""
    c, d = ctx(), body()
    models_dir = c.settings.get("paths")["models_dir"]
    model, job = _local_qwen06(models_dir), None
    if not model:
        info, quant, problems = None, None, []
        for repo in ([d["repo"]] if d.get("repo") else DEFAULT_REPOS):
            try:
                info = c.downloads.client().repo(repo)
            except DownloadError as exc:
                problems.append(f"{repo}: {exc}")
                continue
            quant = pick_quant(info["quants"])
            if quant:
                break
        if not quant:
            return err("Could not find Qwen3-0.6B on Hugging Face. " + "; ".join(problems), 502)
        sizes = {f["name"]: f["size"] for f in info["files"]}
        try:
            job = c.downloads.start(info["id"], [{"name": n, "size": sizes.get(n, 0)} for n in quant["files"]])
        except DownloadError as exc:
            return err(str(exc), 502)
        model = {"path": os.path.join(job["dest"], quant["files"][0]), "kind": "chat", "mmproj": []}
    cfg = c.settings.get("assistant")
    prefab = c.settings.get("prefab")
    prefab = {**prefab, "host": "127.0.0.1", "ctx": str(cfg["context_tokens"]), "cache_type": "",
              "split_mode": "none" if prefab.get("split_mode") else ""}
    cmd = prefab_command(model, prefab, port="${PORT}").to_string(style="odysseus")
    existing = c.store.get_profile(cfg["profile_id"]) if cfg["profile_id"] else None
    profile = c.store.save_profile({
        "profile_id": existing["profile_id"] if existing and existing["name"] == PROFILE_NAME else None,
        "name": PROFILE_NAME, "base_model": "Qwen3-0.6B", "model_id": model_id_for_command(cmd),
        "source": "assistant", "launch_command": cmd, "notes": "Built-in assistant model",
    })
    c.settings.update("assistant", {"mode": "managed", "profile_id": profile["profile_id"]})
    c.event("assistant", f"assistant set to {profile['name']}" + (" (downloading model)" if job else ""))
    return ok(profile=profile, download=job)
