"""Spot a client stuck in a loop: the same request, over and over.

An agent that keeps repeating a tool call (and getting the same error back) sends one request after another of
the same size and gets the same short answer: 67 tokens in and 22 out, 1,240 times in an hour. Real work does not
look like that for long: prompts and answers change size. So a run of ``repeats`` requests in a row from one
client to one model, every one within ``tolerance`` tokens of the last in both what it read and what it wrote, is
called a loop. Embeddings are left out (the same text embedded again is normal), and so are requests that wrote
nothing.
"""
from __future__ import annotations

from typing import Optional


def _same(a: dict, b: dict, tol: int) -> bool:
    return (abs((a.get("input_tokens") or 0) - (b.get("input_tokens") or 0)) <= tol
            and abs((a.get("output_tokens") or 0) - (b.get("output_tokens") or 0)) <= tol)


def find_loops(rows: list[dict], repeats: int = 25, tolerance: int = 3, now: Optional[float] = None,
               fresh_s: float = 300) -> list[dict]:
    """Loops still going in ``rows`` (the request history, any order): one per client and model, as
    {"src", "model", "in", "out", "count", "since", "last"}. A run that ended more than ``fresh_s`` ago is not."""
    groups: dict[tuple, list[dict]] = {}
    for r in rows:
        if not r.get("src") or not r.get("model") or "embedding" in (r.get("path") or ""):
            continue
        groups.setdefault((r["src"], r["model"]), []).append(r)
    out = []
    newest = now if now is not None else max((r.get("ts") or 0 for r in rows), default=0)
    for (src, model), rs in groups.items():
        rs.sort(key=lambda r: r.get("ts") or 0)
        last = rs[-1]
        if not (last.get("output_tokens") or 0) or newest - (last.get("ts") or 0) > fresh_s:
            continue
        run = 1
        for r in reversed(rs[:-1]):
            if not _same(r, last, tolerance):
                break
            run += 1
        if run >= repeats:
            first = rs[-run]
            out.append({"src": src, "model": model, "in": last.get("input_tokens") or 0, "out": last.get("output_tokens") or 0,
                        "count": run, "since": first.get("ts"), "last": last.get("ts")})
    return out


def describe(loop: dict) -> str:
    minutes = max(1, round(((loop.get("last") or 0) - (loop.get("since") or 0)) / 60))
    return (f"{loop['count']} requests in a row of {loop['in']} tokens in / {loop['out']} out "
            f"in {minutes} min, the same each time")


def check(store, settings, engine_url: str, state: dict, event, now: float) -> list[dict]:
    """One look at the last hour of requests: flag new loops, and stop them when the guard is set to.

    ``state`` (kept between looks) is keyed "src|model": {"status": "flagged"|"stopped"|"allowed", ...}. A loop
    a person let run is not flagged again while its requests keep the same size. Returns the loops now going."""
    from .integrations import engine_api
    g = settings.get("guard")
    if not g.get("enabled", True):
        return []
    rows = store.list_requests(since=now - 3600, limit=5000)
    loops = find_loops(rows, int(g.get("repeats") or 25), int(g.get("tolerance") or 3), now=now)
    going = set()
    for lp in loops:
        key = f"{lp['src']}|{lp['model']}"
        going.add(key)
        have = state.get(key)
        if have and have.get("status") == "allowed" and _same({"input_tokens": have["in"], "output_tokens": have["out"]},
                                                              {"input_tokens": lp["in"], "output_tokens": lp["out"]}, int(g.get("tolerance") or 3)):
            have.update(count=lp["count"], last=lp["last"])
            continue
        if have and have.get("status") in ("flagged", "stopped"):
            have.update(count=lp["count"], last=lp["last"])
            continue
        why = describe(lp)
        entry = {**lp, "status": "flagged", "reason": why, "at": now}
        state[key] = entry
        stop = g.get("action", "stop") == "stop" and engine_url and lp["src"].startswith("ip:")
        if stop:
            try:
                engine_api.guard_block(engine_url, lp["src"][3:], lp["model"], why)
                entry["status"] = "stopped"
            except Exception as exc:  # noqa: BLE001 - flagged at least
                entry["error"] = str(exc)
        event("schedule", f"loop guard: {lp['src']} to {lp['model']}: {why}"
              + ("; stopped its requests (Schedule -> Live -> Loop guard to let it run)" if entry["status"] == "stopped" else ""),
              level="warn", data={"src": lp["src"], "model": lp["model"]})
    return loops


__all__ = ["check", "describe", "find_loops"]
