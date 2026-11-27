import os

from src import sysmon


def _proc(root, stat_cpu, procs, mem_kb=(16_000_000, 6_000_000)):
    os.makedirs(root, exist_ok=True)
    with open(os.path.join(root, "stat"), "w") as f:
        f.write("\n".join(stat_cpu) + "\nintr 1 2 3\n")
    with open(os.path.join(root, "meminfo"), "w") as f:
        f.write(f"MemTotal: {mem_kb[0]} kB\nMemAvailable: {mem_kb[1]} kB\nSwapTotal: 0 kB\nSwapFree: 0 kB\n")
    with open(os.path.join(root, "loadavg"), "w") as f:
        f.write("1.50 0.75 0.25 2/300 999\n")
    for pid, (name, ticks, cmd) in procs.items():
        d = os.path.join(root, str(pid))
        os.makedirs(d, exist_ok=True)
        # pid (comm) state ppid ... utime(14) stime(15) ... threads(20) ... rss(24)
        fields = ["R"] + ["0"] * 10 + [str(ticks), "0"] + ["0"] * 4 + ["3"] + ["0"] * 3 + ["2560"]
        with open(os.path.join(d, "stat"), "w") as f:
            f.write(f"{pid} ({name}) " + " ".join(fields) + " 0 0\n")
        with open(os.path.join(d, "cmdline"), "w") as f:
            f.write(cmd.replace(" ", "\0"))


def test_sysmon_top_measures_cpu_between_readings(tmp_path):
    root = str(tmp_path / "proc")
    sysmon._PREV.clear()
    _proc(root, ["cpu  100 0 100 800 0", "cpu0 50 0 50 400 0", "cpu1 50 0 50 400 0"],
          {10: ("llama-server", 100, "llama-server -m /m/a.gguf"), 20: ("sleepy", 5, "")})
    first = sysmon.top(root, now=1000.0)
    assert first["first"] and first["cpu"] is None and first["cores"] == [None, None]
    assert first["mem_total_mb"] == round(16_000_000 / 1024, 1) and first["load"] == [1.5, 0.75, 0.25]

    hz = os.sysconf("SC_CLK_TCK")
    # one second later: cpu0 fully busy, cpu1 idle; llama-server used one second of CPU
    _proc(root, ["cpu  200 0 100 900 0", "cpu0 150 0 50 400 0", "cpu1 50 0 50 500 0"],
          {10: ("llama-server", 100 + hz, "llama-server -m /m/a.gguf"), 20: ("sleepy", 5, "")})
    d = sysmon.top(root, now=1001.0)
    assert not d["first"]
    assert d["cores"] == [100.0, 0.0] and d["cpu"] == 50.0
    top = d["processes"][0]
    assert (top["pid"], top["name"], top["cpu"], top["threads"]) == (10, "llama-server", 100.0, 3)
    assert top["cmd"] == "llama-server -m /m/a.gguf"
    assert d["processes"][1]["cmd"] == "[sleepy]"  # a kernel thread style name when there is no command line


def test_sysmon_smi_reports_a_missing_tool(monkeypatch):
    monkeypatch.setattr(sysmon.gputools, "tool", lambda name: "")
    r = sysmon.smi("nvidia-smi")
    assert r["found"] is False and "not in this container" in r["text"]


def test_sysmon_smi_shows_each_call_like_a_terminal(monkeypatch):
    monkeypatch.setattr(sysmon.gputools, "tool", lambda name: "/opt/rocm/bin/rocm-smi")
    seen = []
    monkeypatch.setattr(sysmon, "_run", lambda argv, timeout=8.0: seen.append(argv) or f"out of {' '.join(argv[1:]) or 'plain'}")
    r = sysmon.smi("rocm-smi")
    assert r["found"] and r["path"] == "/opt/rocm/bin/rocm-smi"
    assert seen == [["/opt/rocm/bin/rocm-smi"], ["/opt/rocm/bin/rocm-smi", "--showmeminfo", "vram"], ["/opt/rocm/bin/rocm-smi", "--showpids"]]
    assert r["text"].startswith("$ rocm-smi\nout of plain")
    assert "$ rocm-smi --showpids\nout of --showpids" in r["text"]


def test_sysmon_api(client, monkeypatch):
    monkeypatch.setattr(sysmon.gputools, "tool", lambda name: "")
    r = client.get("/api/sysmon?tab=rocm-smi").get_json()
    assert r["ok"] and r["tab"] == "rocm-smi" and r["found"] is False
    r = client.get("/api/sysmon?tab=top").get_json()
    assert r["ok"] and "processes" in r
    assert client.get("/api/sysmon?tab=bogus").status_code == 400
