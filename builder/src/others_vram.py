"""How much GPU memory each runner outside Argos holds (Servers -> Others).

A runner Argos does not run itself still sits on the same GPUs. The engine's pool already plans around memory
it did not place (see internal/router/outside.go), but only as "something else". This names it:

- **in another container over SSH**: Argos asks that container, over the same SSH it starts models with, which
  of its processes have GPU memory. The kernel lists it per open GPU file (``drm-memory-vram`` in
  ``/proc/<pid>/fdinfo``), so it needs no rocm-smi there and is exact.
- **a service already running (URL)**: measured on a server you tie it to (its own container with sshd, or
  the machine it runs on), else found among the GPU processes Argos sees: by its network address when Argos
  shares the host's PID namespace, else by its size hint, else as the only unclaimed process left.

A ``Monitor`` reads this every 30 s, so each card shows the memory now and its peak in the last hour.
"""
from __future__ import annotations

import os
import re
import socket
import threading
import time
import urllib.parse
from collections import deque
from typing import Callable, Optional

GIB = 2 ** 30
EVERY_S = 30
KEEP_S = 3600
MATCH_TOLERANCE = 0.4  # a size hint matches a process within this share
SAME = 0.05  # a process seen over SSH and one seen here are the same when their memory is this close

# POSIX sh run in the other container: one "gpu=" line per process with GPU memory there, read from the kernel.
# A file's memory is counted once per DRM client (several fds can share one), per card (drm-pdev).
PROBE = r"""
for d in /proc/[0-9]*; do
  grep -sH -E '^(drm-pdev|drm-client-id|drm-memory-vram|drm-resident-vram|drm-total-vram):' "$d"/fdinfo/* 2>/dev/null
done | awk '
{ i = index($0, ":"); path = substr($0, 1, i - 1); rest = substr($0, i + 1)
  j = index(rest, ":"); k = substr(rest, 1, j - 1); v = substr(rest, j + 1); gsub(/^[ \t]+|[ \t]+$/, "", v)
  split(path, a, "/"); id = a[3] "/" a[5]; ids[id] = a[3]
  if (k == "drm-pdev") pdev[id] = v
  else if (k == "drm-client-id") cl[id] = v
  else { split(v, n, " "); x = n[1] + 0; u = n[2]
         if (u == "MiB") x *= 1024; else if (u == "GiB") x *= 1048576; else if (u == "" || u == "B") x /= 1024
         m[id, k] = x } }
END { for (id in ids) {
        x = m[id, "drm-memory-vram"]; if (x == "") x = m[id, "drm-resident-vram"]; if (x == "") x = m[id, "drm-total-vram"]
        if (x + 0 <= 0) continue
        key = ids[id] SUBSEP pdev[id] SUBSEP cl[id]; if (key in seen) continue; seen[key] = 1
        sum[ids[id] " " pdev[id]] += x }
      for (k in sum) printf "%s %d\n", k, sum[k] }' | while read -r pid pdev kib; do
  printf 'gpu=%s\t%s\t%s\t%s\t%s\n' "$pid" "$pdev" "$kib" "$(cat /proc/$pid/comm 2>/dev/null)" \
    "$(tr '\000' ' ' < /proc/$pid/cmdline 2>/dev/null | cut -c1-300)"
done
echo argos=ok
"""

DRM = os.environ.get("ARGOS_DRM_ROOT") or "/sys/class/drm"


def parse_probe(text: str) -> list[dict]:
    """The probe's "gpu=" lines -> [{"pid", "pdev", "gib", "name", "cmd"}]."""
    out = []
    for line in (text or "").splitlines():
        if not line.startswith("gpu="):
            continue
        parts = (line[4:].split("\t") + ["", "", "", "", ""])[:5]
        if not parts[0].isdigit() or not parts[2].isdigit():
            continue
        out.append({"pid": int(parts[0]), "pdev": parts[1].strip(), "gib": round(int(parts[2]) * 1024 / GIB, 2),
                    "name": parts[3].strip(), "cmd": parts[4].strip()})
    return out


def pdev_cards(root: str = DRM) -> dict[str, str]:
    """PCI address -> card index, numbered like gpu_procs and the engine (cards with VRAM, in DRM order)."""
    cards = []
    try:
        names = os.listdir(root)
    except OSError:
        return {}
    for n in names:
        m = re.fullmatch(r"card(\d+)", n)
        if not m or not os.path.exists(os.path.join(root, n, "device", "mem_info_vram_total")):
            continue
        try:
            pci = os.path.basename(os.path.realpath(os.path.join(root, n, "device")))
        except OSError:
            continue
        cards.append((int(m.group(1)), pci))
    return {pci: str(i) for i, (_, pci) in enumerate(sorted(cards))}


def _addresses(pid: int) -> set[str]:
    """The IPv4 addresses of a process's network namespace (needs the process visible here)."""
    try:
        with open(f"/proc/{pid}/net/fib_trie", encoding="utf-8", errors="replace") as f:
            fib = f.read().splitlines()
    except OSError:
        return set()
    return {fib[i - 1].split()[-1] for i, l in enumerate(fib) if "/32 host LOCAL" in l and i
            and not fib[i - 1].split()[-1].startswith("127.")}


def _resolve(url: str) -> set[str]:
    host = urllib.parse.urlparse(url).hostname or ""
    if not host:
        return set()
    try:
        return {a[4][0] for a in socket.getaddrinfo(host, None, socket.AF_INET)}
    except OSError:
        return set()


_CACHE: dict = {}  # server id -> (time, result): the page and the monitor share a reading for a few seconds
CACHE_S = 10


class Probes:
    """One SSH probe per server per reading (and at most one every CACHE_S seconds)."""

    def __init__(self, servers, cards: dict[str, str]):
        self.servers, self.cards, self.done = servers, cards, {}

    def get(self, server: dict) -> dict:
        sid = server["id"]
        if sid in self.done:
            return self.done[sid]
        hit = _CACHE.get(sid)
        if hit and time.time() - hit[0] < CACHE_S:
            self.done[sid] = hit[1]
            return hit[1]
        res: dict = {"server": server.get("name") or server.get("host"), "processes": [], "error": ""}
        if not self.servers.has_key(sid):
            res["error"] = "no SSH key for this server yet (Servers -> Remote -> Key)"
        else:
            try:
                proc = self.servers.run(self.servers._argv(server), PROBE, 20)
                if "argos=ok" not in (proc.stdout or ""):
                    res["error"] = ((proc.stderr or "").strip().splitlines() or ["no answer"])[-1]
                for p in parse_probe(proc.stdout):
                    p["card"] = self.cards.get(p["pdev"], "")
                    res["processes"].append(p)
            except Exception as exc:  # noqa: BLE001 - shown on the card
                res["error"] = f"could not ask it: {exc}"
        res["total_gib"] = round(sum(p["gib"] for p in res["processes"]), 2)
        self.done[sid] = res
        _CACHE[sid] = (time.time(), res)
        return res


def _program(r: dict) -> str:
    from .runners import REMOTE_BIN
    prog = str((r.get("opts") or {}).get("remote_program") or "").strip() or REMOTE_BIN.get(r["kind"], "")
    return os.path.basename(prog)


def _usage(procs: list[dict], how: str, server: str = "") -> dict:
    cards: dict[str, float] = {}
    for p in procs:
        c = str(p.get("card") or p.get("gpu") or "?")
        cards[c] = round(cards.get(c, 0) + (p.get("gib") or p.get("vram_gib") or 0), 2)
    return {"vram_gib": round(sum(cards.values()), 2), "cards": cards, "how": how, "server": server,
            "processes": [{"pid": p.get("pid"), "name": p.get("name") or "", "gib": p.get("gib") or p.get("vram_gib") or 0,
                           "card": str(p.get("card") or p.get("gpu") or "?")} for p in procs]}


def measure(runners, servers, listing: Optional[dict] = None, cards: Optional[dict] = None) -> dict[str, dict]:
    """runner id -> {"vram_gib", "cards": {card: GiB}, "how", "server", "processes", "error"?} for every runner
    that runs outside Argos. ``listing`` is gpu_procs.listing() (the GPU processes Argos sees); it is tagged
    in place with the runner that owns each process."""
    rec = runners.records()
    outside = {rid: r for rid, r in rec.items() if r.get("enabled", True) and runners.where(r) != "here"}
    if not outside:
        return {}
    probes = Probes(servers, pdev_cards() if cards is None else cards) if servers is not None else None
    procs = (listing or {}).get("processes") or []
    out: dict[str, dict] = {}

    def server_of(sid) -> Optional[dict]:
        if servers is None or not str(sid or "").strip():
            return None
        return next((s for s in servers.store.list_servers() if str(s["id"]) == str(sid) and s.get("host")), None)

    def claim(p: dict, rid: str) -> None:
        if not p.get("owner"):
            p["owner"] = f"others {rid}"
            p["argos"] = True

    def claim_seen_over_ssh(seen: list[dict], rid: Optional[str]) -> None:
        """A process seen over SSH is one of the hidden ones here: same card, same memory."""
        for s in seen:
            for p in procs:
                if p.get("visible", True) or not p.get("vram_gib") or not s["gib"]:
                    continue
                if s.get("card") and str(p.get("gpu")) != s["card"]:
                    continue
                if abs(p["vram_gib"] - s["gib"]) / s["gib"] <= SAME:
                    p.setdefault("seen_on", s.get("server", ""))
                    if rid:
                        claim(p, rid)
                    break

    # every server Argos reaches over SSH: what runs there is that container's, whoever it is
    if probes is not None:
        for s in servers.store.list_servers():
            if s.get("host") and servers.has_key(s["id"]):
                got = probes.get(s)
                claim_seen_over_ssh([{**p, "server": got["server"]} for p in got["processes"]], None)

    for rid, r in outside.items():
        o = r.get("opts") or {}
        where = runners.where(r)
        if where == "server":
            s = server_of(o.get("server"))
            if not s or probes is None:
                out[rid] = {**_usage([], "its server is gone from the Servers page"), "error": "no server"}
                continue
            got = probes.get(s)
            prog = _program(r)
            mine = [p for p in got["processes"] if prog and (prog in p["cmd"] or p["name"] == prog[:15])]
            out[rid] = _usage(mine, f"measured in {got['server']} over SSH" if mine else f"not running in {got['server']} now", got["server"])
            out[rid]["server_total_gib"] = got["total_gib"]
            if got["error"]:
                out[rid]["error"] = got["error"]
            claim_seen_over_ssh([{**p, "server": got["server"]} for p in mine], rid)
            continue
        # a service already running
        s = server_of(o.get("monitor_server"))
        if s and probes is not None:
            got = probes.get(s)
            out[rid] = _usage(got["processes"], f"everything on the GPUs in {got['server']}, measured over SSH", got["server"])
            if got["error"]:
                out[rid]["error"] = got["error"]
            claim_seen_over_ssh([{**p, "server": got["server"]} for p in got["processes"]], rid)
            continue
        ips = _resolve(o.get("url") or "")
        mine = [p for p in procs if p.get("visible", True) and p.get("vram_gib") and not p.get("owner")
                and ips & _addresses(p.get("local_pid") or p["pid"])]
        if mine:
            for p in mine:
                claim(p, rid)
            out[rid] = _usage(mine, "found by its network address")
            continue
        out[rid] = None  # decided below, once every service had its chance

    # what is left: GPU memory nobody claimed, given to the services not yet found
    left = [p for p in procs if not p.get("owner") and not p.get("self") and not p.get("seen_on") and p.get("vram_gib")]
    waiting = [rid for rid, v in out.items() if v is None]
    for rid in list(waiting):
        hint = _float((rec[rid].get("opts") or {}).get("vram_hint"))
        if not hint:
            continue
        best = min(left, key=lambda p: abs(p["vram_gib"] - hint), default=None)
        if best and abs(best["vram_gib"] - hint) / hint <= MATCH_TOLERANCE:
            left.remove(best)
            claim(best, rid)
            out[rid] = _usage([best], f"the process closest to its size hint ({hint:g} GiB)")
            waiting.remove(rid)
    if len(waiting) == 1 and len(left) == 1:
        claim(left[0], waiting[0])
        out[waiting[0]] = _usage(left, "the only GPU process left that Argos did not start")
        waiting = []
    for rid in waiting:
        out[rid] = {**_usage([], "not found"), "vram_gib": None,
                    "error": (f"{len(left)} GPU processes outside Argos could be it. Tie it to a server it runs on, "
                              "give a size hint, or run Argos with pid: host so it is found by its address.")
                    if left else "no GPU process outside Argos right now"}
    return out


def _float(v) -> float:
    try:
        return max(0.0, float(v or 0))
    except (TypeError, ValueError):
        return 0.0


class Monitor:
    """Reads ``measure`` every 30 s while a runner runs outside Argos; keeps an hour of readings per runner."""

    def __init__(self, read: Callable[[], dict]):
        self.read = read
        self.last: dict[str, dict] = {}
        self.at = 0.0
        self.hist: dict[str, deque] = {}
        self.lock = threading.Lock()
        self._stop = threading.Event()

    def tick(self, now: Optional[float] = None) -> dict:
        now = now or time.time()
        got = self.read()
        with self.lock:
            self.last, self.at = got, now
            for rid, u in got.items():
                if u.get("vram_gib") is None:
                    continue
                h = self.hist.setdefault(rid, deque())
                h.append((now, u["vram_gib"]))
                while h and h[0][0] < now - KEEP_S:
                    h.popleft()
        return got

    def view(self) -> dict:
        with self.lock:
            out = {}
            for rid, u in self.last.items():
                h = list(self.hist.get(rid) or [])
                out[rid] = {**u, "peak_gib": max((g for _, g in h), default=None),
                            "history": [[round(t), g] for t, g in h[-120:]]}
            return {"at": self.at, "runners": out}

    def start(self) -> None:
        threading.Thread(target=self._loop, name="others-vram", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.wait(EVERY_S if self.at else 5):
            try:
                self.tick()
            except Exception:  # noqa: BLE001 - monitoring never stops the app
                pass
