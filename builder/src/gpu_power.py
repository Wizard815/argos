"""GPU power profiles: a power cap (and, if wanted, a top core clock) for each model profile, set on the Profiles
page, plus watts and tokens per watt for every benchmark.

Each profile can carry a power setting: the card's own cap ("stock"), a cap in watts, and optionally the highest
core clock (SCLK) the card may use. The setting is not part of the launch command: llama-server cannot set a power
cap. The Governor puts the cards at the setting of whatever profile is running, every few seconds:

- several running profiles with different settings: the most generous one wins (the highest cap, the highest
  clock), so no running model is slowed by another's setting;
- nothing running with a setting: the idle setting, if one is set; otherwise the card's stock values, but only when
  Argos changed them before (a card Argos never touched is left as it is);
- the main switch off: back to stock once, then Argos leaves the cards alone.

A raise (a higher cap, or a clock limit taken off) waits while any GPU's junction is at or above the guard
temperature; a cut is never held back. The fan monitor (fan-bridge, ilofanmonitorgpu) raises the chassis fans with
load, so a raise only waits for the heat that is already there.

Writes go through one of these, picked by Settings -> power -> backend ("auto" tries them in this order):

- fan-bridge: its /api/power. It already has the GPUs' sysfs folders mounted writable, so Argos needs no extra
  mount, and it keeps its perf-on-demand from fighting a clock limit.
- sysfs: hwmon power1_cap and pp_dpm_sclk directly, when /sys/class/drm is writable here (a bare-metal install, or
  the device folders bind-mounted read-write).
- rocm-smi / amd-smi: --setpoweroverdrive / set --power-cap, then --setsclk for the clock (rocm-smi only).

Reads always come from sysfs (read-only is enough) or from fan-bridge.
"""
from __future__ import annotations

import glob
import json
import os
import shutil
import subprocess
import threading
import time
import urllib.request
from typing import Any, Callable, Optional

KEY = "_power_profiles"        # {profile_id: {"on", "cap_w", "sclk_mhz"}}
ENABLED_KEY = "_power_enabled"  # the main switch
IDLE_KEY = "_power_idle"        # {"on", "cap_w", "sclk_mhz"}: the cards with no profile running

CAP_RANGE = (50, 400)    # W; each card's own power1_cap_min/max narrow it further when applied
SCLK_RANGE = (300, 2500)  # MHz
PRESETS = [
    # a starting point for an MI50: generation is memory-bound, so a lower cap mostly slows prompt reading
    {"id": "stock", "label": "Stock (the card's own cap)", "cap_w": None},
    {"id": "balanced", "label": "Balanced, 180 W", "cap_w": 180},
    {"id": "efficient", "label": "Efficient, 150 W", "cap_w": 150},
    {"id": "eco", "label": "Eco, 125 W", "cap_w": 125},
]
SYSFS_ROOT = os.environ.get("ARGOS_GPU_SYSFS", "/sys/class/drm")
AMD = "0x1002"


# ----- settings ------------------------------------------------------------------------------------------------
def table(store) -> dict:
    return {k: v for k, v in (store.get_settings().get(KEY) or {}).items() if isinstance(v, dict)}


def enabled(store) -> bool:
    """On unless turned off. Off: Argos puts the cards back to stock once and then leaves them alone."""
    v = store.get_settings().get(ENABLED_KEY)
    return True if v is None else bool(v)


def idle(store) -> dict:
    v = store.get_settings().get(IDLE_KEY)
    return v if isinstance(v, dict) else {}


def _num(v, lo, hi, what) -> Optional[int]:
    if v in (None, "", "stock", "all"):
        return None
    try:
        n = int(round(float(v)))
    except (TypeError, ValueError):
        raise ValueError(f"{what} is a number from {lo} to {hi}, or empty for the card's own") from None
    if not lo <= n <= hi:
        raise ValueError(f"{what} is from {lo} to {hi}")
    return n


def clean(d: dict) -> dict:
    """A power setting from the page. ``on`` false keeps the values, so switching it on again brings them back."""
    return {"on": bool(d.get("on", True)), "cap_w": _num(d.get("cap_w"), *CAP_RANGE, "the power cap (W)"),
            "sclk_mhz": _num(d.get("sclk_mhz"), *SCLK_RANGE, "the top core clock (MHz)")}


def label(e: Optional[dict]) -> str:
    """How a setting reads in a table: "180 W", "stock", "150 W, ≤1400 MHz"."""
    if not e:
        return "as is"
    s = f"{e['cap_w']} W" if e.get("cap_w") else "stock"
    return s + (f", ≤{e['sclk_mhz']} MHz" if e.get("sclk_mhz") else "")


# ----- what the cards should run at ----------------------------------------------------------------------------
def plan(entries: list[dict], cards: list[dict]) -> dict:
    """{pci: {"cap_w", "sclk_mhz"}} for these settings together: per card the highest cap (stock counted as the
    card's default cap) and the highest clock (None, no limit, beats any number)."""
    out = {}
    for c in cards:
        caps = [e.get("cap_w") or c.get("cap_default_w") or c.get("cap_max_w") for e in entries]
        caps = [x for x in caps if x]
        cap = max(caps) if caps else None
        if cap is not None and c.get("cap_default_w") and cap == c["cap_default_w"] and not any(e.get("cap_w") == cap for e in entries):
            cap = None  # stock won: say stock, so the card's own default is written back
        clocks = [e.get("sclk_mhz") for e in entries]
        sclk = None if not clocks or any(x is None for x in clocks) else max(clocks)
        out[c["pci"]] = {"cap_w": cap, "sclk_mhz": sclk}
    return out


def stock(cards: list[dict]) -> dict:
    return {c["pci"]: {"cap_w": None, "sclk_mhz": None} for c in cards}


def is_raise(card: dict, want: dict) -> bool:
    """Would this give the card more power than it has now?"""
    target = want.get("cap_w") or card.get("cap_default_w") or card.get("cap_max_w") or 0
    if card.get("cap_w") is not None and target > card["cap_w"] + 0.5:
        return True
    return want.get("sclk_mhz") is None and bool(card.get("sclk_limited")) or (
        want.get("sclk_mhz") is not None and card.get("sclk_limit_mhz") is not None and want["sclk_mhz"] > card["sclk_limit_mhz"])


def same(card: dict, want: dict) -> bool:
    """Is the card already there? (Within a watt: the driver rounds.)"""
    cap = want.get("cap_w") or card.get("cap_default_w")
    if cap is not None and card.get("cap_w") is not None and abs(cap - card["cap_w"]) > 1:
        return False
    if want.get("sclk_mhz") is None:
        return not card.get("sclk_limited")
    return card.get("sclk_limit_mhz") == _clock_level_top(card, want["sclk_mhz"])


def _clock_level_top(card: dict, mhz: int) -> Optional[int]:
    levels = card.get("sclk_levels_mhz") or []
    under = [x for x in levels if x <= mhz]
    return max(under) if under else (levels[0] if levels else None)


def hottest(cards: list[dict]) -> Optional[float]:
    ts = [c.get("junction_c") if c.get("junction_c") is not None else c.get("edge_c") for c in cards]
    ts = [t for t in ts if t is not None]
    return max(ts) if ts else None


def decide(want: dict, cards: list[dict], guard_c: float) -> tuple[dict, list[str]]:
    """The cards to write (only those not there yet), and why any raise waits."""
    by = {c["pci"]: c for c in cards}
    hot = hottest(cards)
    out, held = {}, []
    for pci, w in want.items():
        c = by.get(pci)
        if not c or same(c, w):
            continue
        if hot is not None and hot >= guard_c and is_raise(c, w):
            held.append(f"{c.get('card') or pci}: raise held, a GPU is at {hot:.0f} °C (guard {guard_c:.0f} °C)")
            continue
        out[pci] = w
    return out, held


# ----- sysfs: reading (and writing, where it is writable) ------------------------------------------------------
def _read(path: str) -> Optional[str]:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return None


def _f(path: str, scale: float = 1.0) -> Optional[float]:
    v = _read(path)
    try:
        return round(float(v) / scale, 1) if v not in (None, "") else None
    except ValueError:
        return None


def parse_dpm(text: Optional[str]) -> list[tuple[int, int, bool]]:
    """pp_dpm_sclk -> [(level, MHz, active)]: "0: 925Mhz", "8: 1725Mhz *"."""
    out = []
    for line in (text or "").splitlines():
        k, _, rest = line.partition(":")
        if not k.strip().isdigit():
            continue
        mhz = "".join(ch for ch in rest.split("hz")[0].split("Hz")[0] if ch.isdigit())
        if mhz:
            out.append((int(k), int(mhz), "*" in rest))
    return out


def read_device(dev: str, name: str = "") -> Optional[dict]:
    """One AMD card's power state from its sysfs device folder; None if it is not an AMD GPU with a hwmon."""
    if _read(os.path.join(dev, "vendor")) != AMD:
        return None
    hw = sorted(glob.glob(os.path.join(dev, "hwmon", "hwmon*")))
    if not hw:
        return None
    hw = hw[0]
    pci = os.path.basename(os.path.realpath(dev))
    for line in (_read(os.path.join(dev, "uevent")) or "").splitlines():
        if line.startswith("PCI_SLOT_NAME="):
            pci = line.split("=", 1)[1]
    temps = {}
    for inp in glob.glob(os.path.join(hw, "temp*_input")):
        lab = (_read(inp.replace("_input", "_label")) or "").lower()
        if lab:
            temps[lab] = _f(inp, 1000)
    dpm = parse_dpm(_read(os.path.join(dev, "pp_dpm_sclk")))
    perf = _read(os.path.join(dev, "power_dpm_force_performance_level"))
    levels = [m for _, m, _ in dpm]
    return {
        "card": name or os.path.basename(os.path.dirname(dev)), "pci": pci, "device": _read(os.path.join(dev, "device")),
        "cap_w": _f(os.path.join(hw, "power1_cap"), 1e6), "cap_min_w": _f(os.path.join(hw, "power1_cap_min"), 1e6),
        "cap_max_w": _f(os.path.join(hw, "power1_cap_max"), 1e6), "cap_default_w": _f(os.path.join(hw, "power1_cap_default"), 1e6),
        "power_w": _f(os.path.join(hw, "power1_average"), 1e6) or _f(os.path.join(hw, "power1_input"), 1e6),
        "edge_c": temps.get("edge"), "junction_c": temps.get("junction"), "mem_c": temps.get("mem"),
        "sclk_levels_mhz": levels, "sclk_mhz": next((m for _, m, a in dpm if a), None), "perf_level": perf,
        # manual = someone masked the clock levels; sysfs does not say which, the backend that set them does
        "sclk_limited": perf == "manual", "sclk_limit_mhz": None,
        "writable": os.access(os.path.join(hw, "power1_cap"), os.W_OK), "_dev": dev, "_hwmon": hw,
    }


def read_sysfs(root: str = "") -> list[dict]:
    root = root or SYSFS_ROOT
    out = []
    for card in sorted(glob.glob(os.path.join(root, "card*"))):
        if "-" in os.path.basename(card):
            continue  # card1-DP-3 and the like
        c = read_device(os.path.join(card, "device"), os.path.basename(card))
        if c:
            out.append(c)
    return out


def _write(path: str, value: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(value)


def write_sysfs(card: dict, want: dict) -> None:
    """Set one card's cap and clock limit through its sysfs files. Raises OSError / ValueError."""
    cap = want.get("cap_w") or card.get("cap_default_w")
    if cap is not None:
        lo, hi = card.get("cap_min_w"), card.get("cap_max_w")
        if (lo is not None and cap < lo) or (hi is not None and cap > hi):
            raise ValueError(f"{card['card']}: {cap} W is outside what it allows ({lo}-{hi} W)")
        _write(os.path.join(card["_hwmon"], "power1_cap"), str(int(cap * 1_000_000)))
    dev = card["_dev"]
    mhz = want.get("sclk_mhz")
    if mhz is None:
        if card.get("perf_level") == "manual":
            _write(os.path.join(dev, "power_dpm_force_performance_level"), "auto")
        return
    levels = card.get("sclk_levels_mhz") or []
    keep = [str(i) for i, m in enumerate(levels) if m <= mhz] or ["0"]
    _write(os.path.join(dev, "power_dpm_force_performance_level"), "manual")
    _write(os.path.join(dev, "pp_dpm_sclk"), " ".join(keep))


# ----- the backends --------------------------------------------------------------------------------------------
class SysfsBackend:
    name = "sysfs"

    def __init__(self, root: str = ""):
        self.root = root
        self.limits: dict[str, int] = {}  # sysfs cannot say which clock limit Argos set; remembered here

    def read(self) -> dict:
        cards = read_sysfs(self.root)
        for c in cards:
            lim = self.limits.get(c["pci"])
            if c.get("perf_level") == "manual" and lim:
                c["sclk_limited"], c["sclk_limit_mhz"] = True, lim
        return {"cards": cards}

    def apply(self, want: dict) -> list[str]:
        errors = []
        by = {c["pci"]: c for c in self.read()["cards"]}
        for pci, w in want.items():
            c = by.get(pci)
            if not c:
                errors.append(f"{pci}: no such GPU")
                continue
            try:
                write_sysfs(c, w)
                if w.get("sclk_mhz") is None:
                    self.limits.pop(pci, None)
                else:
                    self.limits[pci] = _clock_level_top(c, w["sclk_mhz"]) or w["sclk_mhz"]
            except (OSError, ValueError) as exc:
                errors.append(f"{c['card']}: {exc}")
        return errors


class SmiBackend(SysfsBackend):
    """rocm-smi or amd-smi for the writes, sysfs for the reads. The device index is found from the PCI address."""

    def __init__(self, exe: str, run: Optional[Callable] = None, root: str = ""):
        super().__init__(root)
        self.exe = exe
        self.name = os.path.basename(exe)
        self._run = run or (lambda argv: subprocess.run(argv, capture_output=True, text=True, timeout=30, check=True).stdout)

    def index_of(self) -> dict:
        """{pci: device index} from rocm-smi --showbus (amd-smi: list --json)."""
        out: dict[str, int] = {}
        try:
            if self.name.startswith("amd-smi"):
                for row in json.loads(self._run([self.exe, "list", "--json"]) or "[]"):
                    out[str(row.get("bdf", "")).lower()] = int(row.get("gpu"))
            else:
                for k, v in json.loads(self._run([self.exe, "--showbus", "--json"]) or "{}").items():
                    if k.startswith("card") and isinstance(v, dict):
                        bus = next((x for kk, x in v.items() if "bus" in kk.lower()), "")
                        out[str(bus).lower()] = int(k[4:])
        except (OSError, ValueError, TypeError, subprocess.SubprocessError):
            pass
        return out

    def apply(self, want: dict) -> list[str]:
        errors = []
        idx = self.index_of()
        by = {c["pci"]: c for c in self.read()["cards"]}
        for pci, w in want.items():
            i, c = idx.get(pci.lower()), by.get(pci) or {"card": pci}
            if i is None:
                errors.append(f"{pci}: {self.name} does not list it")
                continue
            cap = w.get("cap_w") or c.get("cap_default_w")
            try:
                if cap is not None:
                    if self.name.startswith("amd-smi"):
                        self._run([self.exe, "set", "-g", str(i), "--power-cap", str(int(cap))])
                    else:
                        self._run([self.exe, "-d", str(i), "--autorespond", "y", "--setpoweroverdrive", str(int(cap))])
                if self.name.startswith("amd-smi"):
                    if w.get("sclk_mhz") is not None or c.get("perf_level") == "manual":
                        errors.append(f"{c.get('card')}: clock limits need rocm-smi, sysfs or fan-bridge; only the cap was set")
                    continue
                if w.get("sclk_mhz") is None:
                    if c.get("perf_level") == "manual":
                        self._run([self.exe, "-d", str(i), "--setperflevel", "auto"])
                    self.limits.pop(pci, None)
                else:
                    levels = c.get("sclk_levels_mhz") or []
                    keep = [str(n) for n, m in enumerate(levels) if m <= w["sclk_mhz"]] or ["0"]
                    self._run([self.exe, "-d", str(i), "--autorespond", "y", "--setperflevel", "manual"])
                    self._run([self.exe, "-d", str(i), "--autorespond", "y", "--setsclk", *keep])
                    self.limits[pci] = _clock_level_top(c, w["sclk_mhz"]) or w["sclk_mhz"]
            except (OSError, subprocess.SubprocessError) as exc:
                errors.append(f"{c.get('card')}: {self.name}: {getattr(exc, 'stderr', '') or exc}".strip())
        return errors


class FanBridgeBackend:
    """fan-bridge's /api/power: it has the GPUs' sysfs folders writable and owns the fans."""
    name = "fan-bridge"

    def __init__(self, url: str, token: str = "", http: Optional[Callable] = None):
        self.url = url.rstrip("/")
        self.token = token
        self._http = http or self._urlopen

    def _urlopen(self, method: str, path: str, payload: Optional[dict] = None) -> dict:
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(self.url + path, data=data, method=method, headers={"Content-Type": "application/json"})
        if self.token:
            req.add_header("X-Power-Token", self.token)
        try:
            with urllib.request.urlopen(req, timeout=5) as r:  # noqa: S310 - a URL the user set
                return json.loads(r.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as exc:
            try:
                return json.loads(exc.read().decode("utf-8") or "{}") | {"_status": exc.code}
            except ValueError:
                raise OSError(f"fan-bridge answered {exc.code}") from None

    def read(self) -> dict:
        d = self._http("GET", "/api/power")
        return {"cards": d.get("cards") or [], "fans": d.get("fans") or {}}

    def apply(self, want: dict) -> list[str]:
        d = self._http("POST", "/api/power", {"cards": want})
        errs = list(d.get("errors") or [])
        if d.get("error"):
            errs.append(str(d["error"]))
        return errs


def fan_state(url: str, http: Optional[Callable] = None) -> dict:
    """The fan monitor's view, for the page: each GPU's temperatures, the fan floor it set and what it is doing."""
    if not url:
        return {}
    try:
        if http:
            s = http("GET", "/api/state")
        else:
            with urllib.request.urlopen(url.rstrip("/") + "/api/state", timeout=3) as r:  # noqa: S310
                s = json.loads(r.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - the page says it could not reach it
        return {"error": str(exc)}
    st = s.get("state") or {}
    ilo = st.get("ilo") or {}
    fans = [f.get("pct") for f in ilo.get("fans") or [] if isinstance(f, dict) and f.get("pct") is not None]
    return {"phase": st.get("phase"), "floor_pct": st.get("floor_pct"), "target_pct": st.get("target_pct"),
            "control_c": st.get("control_c"), "reason": st.get("reason"), "fan_avg_pct": round(sum(fans) / len(fans)) if fans else None,
            "gpus": [{k: g.get(k) for k in ("card", "pci", "edge_c", "junction_c", "memory_c", "power_w", "busy_pct")}
                     for g in st.get("gpus") or []]}


def pick_backend(cfg: dict, which: Optional[Callable] = None):
    """The backend Settings -> power names, or the first that can write ("auto"); None: read-only."""
    which = which or shutil.which
    kind = (cfg.get("backend") or "auto").strip()
    url = (cfg.get("fan_bridge_url") or "").strip()
    if kind == "fan-bridge" or (kind == "auto" and url):
        return FanBridgeBackend(url, cfg.get("fan_bridge_token") or "") if url else None
    if kind == "sysfs":
        return SysfsBackend()
    if kind in ("rocm-smi", "amd-smi"):
        exe = cfg.get(kind.replace("-", "_") + "_path") or which(kind)
        return SmiBackend(exe) if exe else None
    if kind == "auto":
        if any(c.get("writable") for c in read_sysfs()):
            return SysfsBackend()
        for exe in ("rocm-smi", "amd-smi"):
            path = which(exe)
            if path:
                return SmiBackend(path)
    return None


# ----- the governor --------------------------------------------------------------------------------------------
class Governor:
    """Keeps the cards at the running profiles' setting (see the module docstring). ``hold`` pins a setting while a
    benchmark compares one."""

    def __init__(self):
        self.lock = threading.Lock()
        self.touched = False     # Argos changed a card: stock is written back when nothing asks for anything
        self.hold: Optional[dict] = None
        self.last: dict[str, Any] = {}
        self._backend_key = None
        self._backend = None

    def backend(self, cfg: dict):
        key = json.dumps(cfg, sort_keys=True)
        if key != self._backend_key:
            self._backend_key, self._backend = key, pick_backend(cfg)
        return self._backend

    def want(self, store, running_ids: list[str], cards: list[dict]) -> tuple[Optional[dict], str]:
        """(the plan, why) for now; a None plan: leave the cards as they are."""
        if self.hold is not None:
            return plan([self.hold], cards), f"benchmark: {label(self.hold)}"
        if not enabled(store):
            return (stock(cards), "switched off: back to stock") if self.touched else (None, "switched off")
        t = table(store)
        entries = [(pid, t[pid]) for pid in running_ids if t.get(pid, {}).get("on")]
        if entries:
            return plan([e for _, e in entries], cards), "running: " + ", ".join(sorted({label(e) for _, e in entries}))
        i = idle(store)
        if i.get("on"):
            return plan([i], cards), f"idle: {label(i)}"
        return (stock(cards), "nothing running with a setting: stock") if self.touched else (None, "nothing running with a setting")

    def tick(self, store, cfg: dict, running_ids: list[str], log: Optional[Callable] = None) -> dict:
        with self.lock:
            b = self.backend(cfg)
            if b is None:
                self.last = {"ts": time.time(), "backend": None, "why": "no way to write power settings here (see Settings -> power)"}
                return self.last
            try:
                cards = b.read()["cards"]
            except Exception as exc:  # noqa: BLE001 - shown on the page
                self.last = {"ts": time.time(), "backend": b.name, "error": f"could not read the GPUs: {exc}"}
                return self.last
            want, why = self.want(store, running_ids, cards)
            todo, held = decide(want or {}, cards, float(cfg.get("guard_c") or 95))
            errors = []
            if todo:
                try:
                    errors = b.apply(todo)
                except Exception as exc:  # noqa: BLE001
                    errors = [str(exc)]
                if len(errors) < len(todo):  # at least one card took it
                    self.touched = any(w.get("cap_w") is not None or w.get("sclk_mhz") is not None for w in (want or {}).values())
                    if log:
                        log(f"GPU power: {why}" + (f" ({len(errors)} error(s))" if errors else ""))
            self.last = {"ts": time.time(), "backend": b.name, "why": why, "applied": todo, "held": held, "errors": errors}
            return self.last


GOV = Governor()


# ----- benchmarks: watts and tokens per watt -------------------------------------------------------------------
BUSY_UTIL = 20.0  # % GPU use that counts as working on the test (loading and idle samples are left out)


def watts_between(samples: list[dict]) -> tuple[Optional[float], Optional[float]]:
    """(average watts of the GPUs that worked, their hottest temperature) from raw GPU samples of a test's window.
    Only the GPUs that were busy count, and only the readings while one of them was: a GPU that sat idle beside it,
    and the seconds spent loading the model, would make the card look more efficient than it is."""
    if not samples:
        return None, None
    busy = {s["gpu"] for s in samples if (s.get("util") or 0) >= BUSY_UTIL}
    gpus = busy or {s["gpu"] for s in samples}
    by_ts: dict[float, list] = {}
    for s in samples:
        if s["gpu"] in gpus:
            by_ts.setdefault(round(s["ts"], 1), []).append(s)
    sums = []
    for rows in by_ts.values():
        if busy and not any((r.get("util") or 0) >= BUSY_UTIL for r in rows):
            continue
        w = [r["power_w"] for r in rows if r.get("power_w") is not None]
        if w:
            sums.append(sum(w))
    temps = [s["temp_c"] for s in samples if s["gpu"] in gpus and s.get("temp_c") is not None]
    return (round(sum(sums) / len(sums), 1) if sums else None), (max(temps) if temps else None)


def add_power(rows: list[dict], results: list[dict], run_start: float, run_end: float, samples_of: Callable,
              power_label: str = "") -> list[dict]:
    """Benchmark rows with the watts measured while each test ran, and its tokens per watt (tokens per joule)."""
    whole = None
    for row, r in zip(rows, results):
        w, t = watts_between(samples_of(r.get("t_start") or run_start, r.get("t_end") or run_end))
        if w is None:  # the test was shorter than a GPU reading: the whole run's average instead
            whole = whole or watts_between(samples_of(run_start, run_end))
            w, t = whole
        row["power_label"] = power_label
        if w:
            row["power_w"] = w
            if row.get("tokens_per_second"):
                row["tokens_per_watt"] = round(row["tokens_per_second"] / w, 4)
            if row.get("prompt_per_second"):
                row["prompt_per_watt"] = round(row["prompt_per_second"] / w, 4)
            row["note"] = (row.get("note") or "") + f" · {w:.0f} W" + (f" ({power_label})" if power_label else "")
        if t is not None:
            row["temp_c"] = t
    return rows


def compare(runs: list[dict]) -> list[dict]:
    """Benchmarks grouped by profile and power setting: the best generation and prompt speeds, their watts and
    tokens per watt, so the settings of one profile sit side by side."""
    groups: dict[tuple, dict] = {}
    for r in runs:
        if not r.get("power_w"):
            continue
        k = (r.get("profile_name") or r.get("base_model") or "", r.get("power_label") or "as is")
        g = groups.setdefault(k, {"profile": k[0], "power": k[1], "runs": 0, "tg": None, "pp": None,
                                  "tg_w": None, "pp_w": None, "tok_per_w": None, "pp_per_w": None, "temp_c": None, "last": 0})
        g["runs"] += 1
        g["last"] = max(g["last"], r.get("created_at") or 0)
        if r.get("temp_c") is not None:
            g["temp_c"] = max(g["temp_c"] or 0, r["temp_c"])
        if r.get("tokens_per_second") and (g["tg"] is None or r["tokens_per_second"] > g["tg"]):
            g["tg"], g["tg_w"], g["tok_per_w"] = r["tokens_per_second"], r["power_w"], r.get("tokens_per_watt")
        if r.get("prompt_per_second") and (g["pp"] is None or r["prompt_per_second"] > g["pp"]):
            g["pp"], g["pp_w"], g["pp_per_w"] = r["prompt_per_second"], r["power_w"], r.get("prompt_per_watt")
    return sorted(groups.values(), key=lambda g: (g["profile"].lower(), -(g["tok_per_w"] or 0)))


__all__ = ["KEY", "ENABLED_KEY", "IDLE_KEY", "PRESETS", "GOV", "Governor", "add_power", "clean", "compare", "decide",
           "enabled", "fan_state", "idle", "label", "pick_backend", "plan", "read_sysfs", "table", "watts_between"]
