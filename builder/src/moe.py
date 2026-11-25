"""MoE offload for a model, set once on the Profiles page instead of in each profile.

A mixture-of-experts model can keep some of its expert weights in system RAM (llama-server's --n-cpu-moe N: the
experts of the first N layers; --cpu-moe: all of them). That frees VRAM for a little speed. Profiles -> MoE offload
sets it per model file as a share of its MoE layers, and every profile of that model runs with it, except the ones
ticked as exempt, which keep what their own command says. "Default" (no share set) leaves every profile alone.

The setting is applied where profiles go to the engine (/api/cookbook/state), so saved profiles never change. The
engine restarts a model whose command changed: llama-server cannot move experts while it runs.
"""
from __future__ import annotations

import os
from typing import Optional

from .command import parse_command
from .gguf import read_moe

KEY = "_moe_overrides"
GIB = 2 ** 30


def overrides(store) -> dict:
    return {k: v for k, v in (store.get_settings().get(KEY) or {}).items() if isinstance(v, dict)}


def layers_for(moe: dict, pct: int) -> tuple[int, bool]:
    """(--n-cpu-moe N, use --cpu-moe) for ``pct`` percent of the model's MoE layers on the CPU. N counts layer
    indices, and the first layers of some models are dense, so N is the index after the last MoE layer moved."""
    moe_layers = moe["moe_layers"]
    k = round(len(moe_layers) * max(0, min(100, pct)) / 100)
    if k <= 0:
        return 0, False
    if k >= len(moe_layers):
        return 0, True
    return moe_layers[k - 1] + 1, False


def own_share(cmd, moe: dict) -> int:
    """The share of MoE layers a command already keeps on the CPU, in percent."""
    if cmd.has("cpu-moe"):
        return 100
    n = str(cmd.get("n-cpu-moe") or "").strip()
    if not n.isdigit() or not moe["moe_layers"]:
        return 0
    return round(100 * sum(1 for i in moe["moe_layers"] if i < int(n)) / len(moe["moe_layers"]))


def apply(command: str, profile_id: str, table: dict) -> str:
    """The command a profile runs with its model's MoE setting, or the command as it is."""
    try:
        cmd = parse_command(command)
    except ValueError:
        return command
    path = cmd.summary().get("model") or ""
    o = table.get(path)
    if not o or o.get("pct") is None or profile_id in (o.get("exempt") or []):
        return command
    moe = read_moe(path)
    if not moe:
        return command
    n, every = layers_for(moe, int(o["pct"]))
    cmd.remove("n-cpu-moe")
    cmd.remove("cpu-moe")
    if every:
        cmd.set("cpu-moe", True)
    elif n:
        cmd.set("n-cpu-moe", n)
    return cmd.to_string()


def apply_all(profiles: list[dict], table: dict) -> list[dict]:
    if not table:
        return profiles
    return [{**p, "launch_command": apply(p["launch_command"], p["profile_id"], table)} for p in profiles]


def memory() -> dict:
    """System RAM in GiB (total, available), for the chart; empty where /proc/meminfo is missing."""
    out = {}
    try:
        with open("/proc/meminfo", encoding="utf-8") as f:
            for line in f:
                k, _, v = line.partition(":")
                if k in ("MemTotal", "MemAvailable"):
                    out["total" if k == "MemTotal" else "available"] = round(int(v.split()[0]) * 1024 / GIB, 1)
    except OSError:
        pass
    return out


def view(profiles: list[dict], table: dict) -> list[dict]:
    """Every MoE model that has profiles: its layout, its setting, and each profile's GPU and RAM share."""
    from .schedule_rows import estimate_command
    by_path: dict[str, list] = {}
    for p in profiles:
        try:
            cmd = parse_command(p["launch_command"])
        except ValueError:
            continue
        path = cmd.summary().get("model") or ""
        if path.lower().endswith(".gguf"):
            by_path.setdefault(path, []).append((p, cmd))
    out = []
    for path, items in sorted(by_path.items()):
        moe = read_moe(path)
        if not moe:
            continue
        o = table.get(path) or {}
        exempt = set(o.get("exempt") or [])
        # GiB moved to RAM when the first k MoE layers' experts go there: the chart moves with the slider
        cum, total = [0.0], 0
        for i in moe["moe_layers"]:
            total += moe["expert_bytes"][i]
            cum.append(round(total / GIB, 3))
        rows = []
        live = {p["profile_id"]: apply(p["launch_command"], p["profile_id"], table) for p, _ in items}
        for p, cmd in items:
            bare = cmd.copy()
            bare.remove("n-cpu-moe")
            bare.remove("cpu-moe")
            full = estimate_command(bare.to_string())
            try:
                served = parse_command(live[p["profile_id"]])
                runs = "--cpu-moe" if served.has("cpu-moe") else (f"--n-cpu-moe {served.get('n-cpu-moe')}" if served.get("n-cpu-moe") else "all on the GPU")
            except ValueError:
                runs = ""
            rows.append({"profile_id": p["profile_id"], "name": p["name"], "exempt": p["profile_id"] in exempt, "runs_with": runs,
                         "own_pct": own_share(cmd, moe), "full_gib": full.get("gib"), "kv_gib": full.get("kv_gib"),
                         "ok": full.get("ok", False), "error": full.get("error", "")})
        out.append({"path": path, "name": os.path.basename(path), "layers": moe["layers"],
                    "moe_layers": len(moe["moe_layers"]), "expert_gib": round(moe["expert_total"] / GIB, 2),
                    "other_gib": round(moe["other_bytes"] / GIB, 2), "cum_gib": cum,
                    "pct": o.get("pct"), "last_pct": o.get("last_pct", o.get("pct")), "profiles": rows})
    return out


def clean(d: dict, profiles: list[dict]) -> dict:
    pct = d.get("pct")
    if pct is not None:
        try:
            pct = int(round(float(pct)))
        except (TypeError, ValueError):
            raise ValueError("pct is a number from 0 to 100, or null for each profile's own setting") from None
        if not 0 <= pct <= 100:
            raise ValueError("pct is from 0 to 100")
    ids = {p["profile_id"] for p in profiles}
    last = d.get("last_pct")
    try:
        last = None if last is None else max(0, min(100, int(round(float(last)))))
    except (TypeError, ValueError):
        last = None
    # the slider and the exempt profiles are kept while Default is on, so turning it off again brings them back
    return {"pct": pct, "last_pct": pct if pct is not None else last,
            "exempt": [str(x) for x in d.get("exempt") or [] if str(x) in ids]}


__all__ = ["KEY", "apply", "apply_all", "layers_for", "memory", "overrides", "view", "clean"]
