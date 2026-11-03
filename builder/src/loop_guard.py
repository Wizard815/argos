"""Spot a client stuck in a loop: the same request, over and over.

An agent that keeps repeating a tool call (and getting the same error back) sends one request after another of
about the same size and gets the same short answer: 67 tokens in and 22 out, 1,240 times in an hour; or 70-83 in
(a counter in the tool result moves) and exactly 61 out, every 6 seconds. Real work does not look like that for
long: answers change size. So ``repeats`` requests from one client to one model that all wrote within a few tokens
of the same answer size, and read about the same (within a quarter), is called a loop. A few other requests in
between (another chat of the same client on the model's other slot) do not end the run. Embeddings are left out
(the same text embedded again is normal), and so are requests that wrote nothing.
"""
from __future__ import annotations

from typing import Optional

IN_SHARE = 0.25   # what a request read may differ by this share of the last one's
OUT_SHARE = 0.05  # what it wrote, by this share: an answer of the same size is the loop's mark


def _same(a: dict, b: dict, tol: int) -> bool:
    ai, bi = a.get("input_tokens") or 0, b.get("input_tokens") or 0
    ao, bo = a.get("output_tokens") or 0, b.get("output_tokens") or 0
    return abs(ai - bi) <= max(tol, IN_SHARE * bi) and abs(ao - bo) <= max(tol, OUT_SHARE * bo)


def find_loops(rows: list[dict], repeats: int = 25, tolerance: int = 3, now: Optional[float] = None,
               fresh_s: float = 300) -> list[dict]:
    """Loops still going in ``rows`` (the request history, any order): one per client and model, as
    {"src", "model", "in", "in_low", "in_high", "out", "count", "since", "last"}. A run that ended more than
    ``fresh_s`` ago is not."""
    groups: dict[tuple, list[dict]] = {}
    for r in rows:
        if not r.get("src") or not r.get("model") or "embedding" in (r.get("path") or ""):
            continue
        groups.setdefault((r["src"], r.get("client") or "", r["model"]), []).append(r)
    out = []
    newest = now if now is not None else max((r.get("ts") or 0 for r in rows), default=0)
    for (src, app, model), rs in groups.items():
        rs.sort(key=lambda r: r.get("ts") or 0)
        # the newest request that wrote something is the shape to match (the other chat may have just finished)
        tail = [r for r in rs[-6:] if r.get("output_tokens")]
        if not tail or newest - (rs[-1].get("ts") or 0) > fresh_s:
            continue
        shape = max(tail, key=lambda r: sum(1 for x in tail if _same(x, r, tolerance)))
        matched, misses = [], 0
        for r in reversed(rs):
            if _same(r, shape, tolerance):
                matched.append(r)
            else:
                misses += 1
                if misses > 3 + len(matched) // 4:  # mostly something else: the run is over
                    break
        if len(matched) >= repeats:
            ins = [m.get("input_tokens") or 0 for m in matched]
            out.append({"src": src, "client": app, "model": model, "in": shape.get("input_tokens") or 0, "in_low": min(ins), "in_high": max(ins),
                        "out": shape.get("output_tokens") or 0, "count": len(matched),
                        "since": matched[-1].get("ts"), "last": matched[0].get("ts")})
    return out


def describe(loop: dict) -> str:
    minutes = max(1, round(((loop.get("last") or 0) - (loop.get("since") or 0)) / 60))
    lo, hi = loop.get("in_low", loop["in"]), loop.get("in_high", loop["in"])
    read = f"{loop['in']} tokens in" if lo == hi else f"{lo}-{hi} tokens in"
    return f"{loop['count']} requests of {read} / {loop['out']} out in {minutes} min, about the same each time"


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
        key = f"{lp['src']}|{lp.get('client') or ''}|{lp['model']}"
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
                engine_api.guard_block(engine_url, lp["src"][3:], lp["model"], why, client=lp.get("client") or "")
                entry["status"] = "stopped"
            except Exception as exc:  # noqa: BLE001 - flagged at least
                entry["error"] = str(exc)
        who = lp["src"] + (f" ({lp['client']})" if lp.get("client") else "")
        event("schedule", f"loop guard: {who} to {lp['model']}: {why}"
              + ("; stopped its requests (Schedule -> Live -> Loop guard to let it run)" if entry["status"] == "stopped" else ""),
              level="warn", data={"src": lp["src"], "client": lp.get("client") or "", "model": lp["model"]})
    return loops


__all__ = ["check", "describe", "find_loops"]
