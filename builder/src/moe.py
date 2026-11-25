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


def view(profiles: list[dict], table: dict, on: bool = True) -> list[dict]:
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
        live = {p["profile_id"]: HOLD.served.get(p["profile_id"]) or (apply(p["launch_command"], p["profile_id"], table) if on else p["launch_command"])
                for p, _ in items}
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
            nxt = HOLD.pending.get(p["profile_id"])
            rows.append({"profile_id": p["profile_id"], "name": p["name"], "exempt": p["profile_id"] in exempt, "runs_with": runs,
                         "next": flags_of(nxt["command"]) if nxt else "", "quiet_s": nxt["quiet_s"] if nxt else None,
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
        except (TypeError, ValueError, OverflowError):
            raise ValueError("pct is a number from 0 to 100, or null for each profile's own setting") from None
        if not 0 <= pct <= 100:
            raise ValueError("pct is from 0 to 100")
    ids = {p["profile_id"] for p in profiles}
    last = d.get("last_pct")
    try:
        last = None if last is None else max(0, min(100, int(round(float(last)))))
    except (TypeError, ValueError, OverflowError):
        last = None
    # the slider and the exempt profiles are kept while Default is on, so turning it off again brings them back
    return {"pct": pct, "last_pct": pct if pct is not None else last,
            "exempt": [str(x) for x in d.get("exempt") or [] if str(x) in ids]}


__all__ = ["KEY", "apply", "apply_all", "layers_for", "memory", "overrides", "view", "clean"]


# ----- the main switch -------------------------------------------------------------------------------------------
ENABLED_KEY = "_moe_enabled"


def enabled(store) -> bool:
    """Profiles -> MoE offload's main switch (on unless turned off). Off: no model's setting is applied, and every
    profile runs with its own command (which may carry a MoE value saved into the profile)."""
    v = store.get_settings().get(ENABLED_KEY)
    return True if v is None else bool(v)


# ----- a profile's own value, saved into its command -------------------------------------------------------------
def set_own(command: str, pct: Optional[int]) -> str:
    """The command with ``pct`` percent of its MoE layers in RAM written into it (0 or None: the flag taken out)."""
    cmd = parse_command(command)
    path = cmd.summary().get("model") or ""
    moe = read_moe(path)
    if not moe:
        raise ValueError("not a mixture-of-experts model")
    cmd.remove("n-cpu-moe")
    cmd.remove("cpu-moe")
    if pct:
        n, every = layers_for(moe, int(pct))
        if every:
            cmd.set("cpu-moe", True)
        elif n:
            cmd.set("n-cpu-moe", n)
    return cmd.to_string()


# ----- a running model keeps its command until it has been idle a while -------------------------------------------
IDLE_S = 30  # the default; Profiles -> MoE offload -> "Reload a model in use after" changes it
IDLE_KEY = "_moe_idle_s"
IDLE_MAX = 3600


def idle_s(store) -> int:
    """How long a running model must go unused before it reloads with a new MoE setting (seconds; 0: at once)."""
    v = store.get_settings().get(IDLE_KEY)
    try:
        return max(0, min(IDLE_MAX, int(v))) if v is not None else IDLE_S
    except (TypeError, ValueError):
        return IDLE_S


class Activity:
    """When each model name was last asked for, and how many requests are running on it, as the builder forwards
    them (engine_proxy)."""

    def __init__(self):
        import threading
        self.lock = threading.Lock()
        self.last: dict[str, float] = {}
        self.busy: dict[str, int] = {}

    def start(self, name: str) -> None:
        import time
        with self.lock:
            self.busy[name] = self.busy.get(name, 0) + 1
            self.last[name] = time.time()

    def end(self, name: str) -> None:
        import time
        with self.lock:
            self.busy[name] = max(0, self.busy.get(name, 0) - 1)
            self.last[name] = time.time()

    def quiet_for(self, names: set) -> float:
        """Seconds since any of these names was last used; 0 while one is busy; a large number if never."""
        import time
        with self.lock:
            if any(self.busy.get(n) for n in names):
                return 0.0
            seen = [self.last[n] for n in names if n in self.last]
        return time.time() - max(seen) if seen else 1e9


ACTIVITY = Activity()


def variant_names(command: str, name: str) -> set:
    """The names a profile's model is asked for by: <model>--<label> and the label alone."""
    from .command import swap_model_id
    from .cookbook import _label_key
    try:
        path = parse_command(command).summary().get("model") or ""
    except ValueError:
        return set()
    label = _label_key(name)
    vid = f"{swap_model_id(path)}--{label}"
    return {vid, label}


class Holder:
    """Which command the engine is given for each profile. A new MoE setting for a profile whose model is running
    and was used in the last ``idle`` seconds (IDLE_S unless set) waits (``pending``) and the old command is served, so the model is not
    reloaded under a chat; once it is quiet (or stopped), the new command goes out and the engine reloads it."""

    def __init__(self):
        import threading
        self.served: dict[str, str] = {}
        self.pending: dict[str, dict] = {}
        self.released: dict[str, bool] = {}  # held, then let go -> was its model running: needs the new command, a reload
        self.lock = threading.Lock()

    def take_released(self) -> dict:
        with self.lock:
            out, self.released = self.released, {}
            return out

    def pick(self, profiles: list[dict], wanted: list[dict], running, quiet_for, now: Optional[float] = None,
             idle: float = IDLE_S) -> list[dict]:
        import time
        now = now or time.time()
        want = {p["profile_id"]: p["launch_command"] for p in wanted}
        out = []
        for p in profiles:
            pid, cmd = p["profile_id"], want.get(p["profile_id"], p["launch_command"])
            have = self.served.get(pid)
            if have is None or have == cmd:
                self.served[pid] = cmd
                self.pending.pop(pid, None)
                out.append({**p, "launch_command": cmd})
                continue
            names = variant_names(have, p["name"])
            run = running()
            if run is None:
                # the engine did not say what runs: keep what it has until it does, rather than take a busy model
                # for a stopped one (its new command would go out mid-chat, and it would not be loaded again)
                self.pending[pid] = {"command": cmd, "since": self.pending.get(pid, {}).get("since", now),
                                     "quiet_s": self.pending.get(pid, {}).get("quiet_s", 0)}
                out.append({**p, "launch_command": have})
                continue
            quiet = quiet_for(names)
            if (names & run) and quiet < idle:
                self.pending[pid] = {"command": cmd, "since": self.pending.get(pid, {}).get("since", now),
                                     "quiet_s": round(quiet if quiet < 1e8 else 0, 1)}
                out.append({**p, "launch_command": have})
            else:
                self.served[pid] = cmd
                self.pending.pop(pid, None)
                # a running model takes its new command now (quiet, or let go after waiting): the engine stops it
                # when it takes the command, so it is loaded again with it (app.py's watch)
                if names & run:
                    with self.lock:
                        self.released[pid] = True
                out.append({**p, "launch_command": cmd})
        return out


HOLD = Holder()


def flags_of(command: str) -> str:
    try:
        c = parse_command(command)
    except ValueError:
        return ""
    return "--cpu-moe" if c.has("cpu-moe") else (f"--n-cpu-moe {c.get('n-cpu-moe')}" if c.get("n-cpu-moe") else "all on the GPU")


# -lm modes that memory-map the file (auto is the default): locking then pins the whole file, the part
# copied to the GPU too. The others read the file into buffers, and locking pins only the ones on the CPU.
MAPPED_LOAD_MODES = {"", "auto", "mmap", "mmap+mlock"}
MLOCK_LOAD_MODES = {"mlock", "mmap+mlock"}
CACHE_RAM_DEFAULT_MIB = 8192


def builder_memory(command: str) -> dict:
    """System RAM a builder command takes: the experts it keeps on the CPU, what --mlock would pin, the host
    prompt cache (--cache-ram) and the MoE expert cache (--moe-cache-mib), next to what is used and free now.

    ``moe`` carries the model's MoE layout so the builder can draw the same slider as Profiles -> MoE offload.
    """
    from .schedule_rows import file_bytes, moe_cpu_bytes
    from .gguf import read_moe
    try:
        cmd = parse_command(command)
    except ValueError as e:
        return {"error": str(e), "ram": memory()}
    path = cmd.summary().get("model") or ""
    gguf = path.lower().endswith(".gguf")
    file_gib = round(file_bytes(path) / GIB, 2) if gguf else 0.0
    moe = read_moe(path) if gguf else None
    cpu_experts = moe_cpu_bytes(cmd, path) / GIB
    ngl = str(cmd.get("n-gpu-layers") or "").strip()
    all_cpu = ngl == "0"
    cpu_weights = file_gib if all_cpu else cpu_experts
    load_mode = str(cmd.get("load-mode") or "").strip()
    mapped = not cmd.has("no-mmap") and load_mode in MAPPED_LOAD_MODES
    # llama.cpp now spells it -lm mlock / -lm mmap+mlock; older builds took --mlock
    locked = cmd.has("mlock") or load_mode in MLOCK_LOAD_MODES
    mlock_gib = file_gib if mapped else cpu_weights

    cram_raw = cmd.get("cache-ram")
    try:
        cram = int(str(cram_raw).strip()) if cram_raw not in (None, "") else CACHE_RAM_DEFAULT_MIB
    except ValueError:
        cram = CACHE_RAM_DEFAULT_MIB
    try:
        mcache = max(0, int(str(cmd.get("moe-cache-mib") or "0").strip()))
    except ValueError:
        mcache = 0

    out = {"model": path, "file_gib": file_gib, "mapped": mapped, "load_mode": load_mode or "auto",
           "cpu_weights_gib": round(cpu_weights, 2), "mlock": locked, "mlock_flag": cmd.has("mlock"), "mlock_gib": round(mlock_gib, 2),
           "cache_ram_mib": cram, "cache_ram_default": cram_raw in (None, ""), "moe_cache_mib": mcache,
           "ram": memory(), "moe": None}
    if moe:
        cum, total = [0.0], 0
        for i in moe["moe_layers"]:
            total += moe["expert_bytes"][i]
            cum.append(round(total / GIB, 3))
        n = str(cmd.get("n-cpu-moe") or "").strip()
        k = len(moe["moe_layers"]) if cmd.has("cpu-moe") else (
            sum(1 for i in moe["moe_layers"] if i < int(n)) if n.isdigit() else 0)
        out["moe"] = {"moe_layers": moe["moe_layers"], "expert_gib": round(moe["expert_total"] / GIB, 2),
                      "cum_gib": cum, "k": k}
    return out
