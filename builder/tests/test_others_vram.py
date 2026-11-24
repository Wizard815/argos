import subprocess

from src import others_vram
from src.others_vram import Monitor, measure, parse_probe
from src.runners import Runners


class _Store:
    def __init__(self):
        self.s = {}

    def get_settings(self):
        return self.s

    def set_setting(self, k, v):
        self.s[k] = v


class _Settings:
    def __init__(self, root):
        self.root = root

    def get(self, group):
        return {"paths": {"runners_dir": self.root, "models_dir": ""}}.get(group, {})


class _ServerStore:
    def __init__(self, servers):
        self._s = servers

    def list_servers(self):
        return self._s


class _Servers:
    """Answers the probe with ``out`` for every server; counts the SSH calls."""

    def __init__(self, servers, out):
        self.store, self.out, self.calls = _ServerStore(servers), out, 0

    def has_key(self, sid):
        return True

    def _argv(self, server):
        return ["ssh", server["host"]]

    def run(self, argv, stdin, timeout):
        self.calls += 1
        return subprocess.CompletedProcess(argv, 0, self.out + "argos=ok\n", "")


SRV = {"id": 3, "name": "llamacpp", "host": "root@mx-llamacpp-ssh", "port": 22, "gpu": "rocm"}
PROBED = ("gpu=41\t0000:03:00.0\t3145728\twhisper-server\t/app/whisper/build/bin/whisper-server --port 5900 -m /app/models/ggml-large-v3.bin\n"
          "gpu=42\t0000:83:00.0\t20971520\tllama-server\t/app/llama-server -m /app/models/q.gguf\n")


def _runners(tmp_path, opts, kind="whisper"):
    r = Runners(_Store(), _Settings(str(tmp_path)), lambda *a, **k: None, lambda: "", lambda d: [])
    rec = r.records()
    rec[kind]["opts"] = opts
    r.save(rec)
    return r


def setup_function(_):
    others_vram._CACHE.clear()


def test_the_probe_lines_are_read():
    got = parse_probe(PROBED + "noise\ngpu=x\tbad\n")
    assert [(p["pid"], p["gib"], p["name"]) for p in got] == [(41, 3.0, "whisper-server"), (42, 20.0, "llama-server")]


def test_whisper_over_ssh_is_measured_in_its_container_and_named_on_the_gpus(tmp_path):
    r = _runners(tmp_path, {"where": "server", "server": "3"})
    s = _Servers([SRV], PROBED)
    r.servers = s
    listing = {"processes": [
        {"pid": 9001, "host_pid": 9001, "visible": False, "gpu": "0", "vram_gib": 3.02, "owner": None},
        {"pid": 9002, "host_pid": 9002, "visible": False, "gpu": "1", "vram_gib": 20.1, "owner": None}]}
    u = measure(r, s, listing, cards={"0000:03:00.0": "0", "0000:83:00.0": "1"})["whisper"]
    assert u["vram_gib"] == 3.0 and u["cards"] == {"0": 3.0} and u["server_total_gib"] == 23.0
    assert "over SSH" in u["how"]
    assert listing["processes"][0]["owner"] == "others whisper"
    assert listing["processes"][1].get("owner") is None and listing["processes"][1]["seen_on"] == "llamacpp"
    assert s.calls == 1, "one probe per server per reading"


def test_a_service_tied_to_its_own_server_counts_everything_there(tmp_path):
    r = _runners(tmp_path, {"where": "url", "url": "http://kokoro:8880", "monitor_server": "3"}, kind="kokoro")
    s = _Servers([SRV], "gpu=7\t0000:03:00.0\t1572864\tpython\tuvicorn api.src.main:app\n")
    u = measure(r, s, {"processes": []}, cards={"0000:03:00.0": "0"})["kokoro"]
    assert u["vram_gib"] == 1.5 and u["cards"] == {"0": 1.5}


def test_a_service_is_found_by_its_size_hint_or_as_the_only_one_left(tmp_path, monkeypatch):
    monkeypatch.setattr(others_vram, "_resolve", lambda url: set())
    procs = lambda: {"processes": [  # noqa: E731
        {"pid": 1, "visible": False, "gpu": "0", "vram_gib": 21.0, "owner": "model qwen"},
        {"pid": 2, "visible": False, "gpu": "1", "vram_gib": 1.4, "owner": None}]}
    r = _runners(tmp_path, {"where": "url", "url": "http://kokoro:8880"}, kind="kokoro")
    listing = procs()
    u = measure(r, None, listing)["kokoro"]
    assert u["vram_gib"] == 1.4 and "only GPU process left" in u["how"] and listing["processes"][1]["owner"] == "others kokoro"
    rec = r.records()
    rec["whisper"]["opts"] = {"where": "url", "url": "http://whisper:8080", "vram_hint": "3"}
    rec["kokoro"]["opts"]["vram_hint"] = "1.5"
    r.save(rec)
    got = measure(r, None, procs())
    assert got["kokoro"]["vram_gib"] == 1.4 and "size hint" in got["kokoro"]["how"]
    assert got["whisper"]["vram_gib"] is None and got["whisper"]["error"]


def test_the_monitor_keeps_the_peak_of_the_last_hour():
    vals = iter([{"kokoro": {"vram_gib": 1.5}}, {"kokoro": {"vram_gib": 2.5}}, {"kokoro": {"vram_gib": 1.0}}])
    m = Monitor(lambda: next(vals))
    m.tick(1000)
    m.tick(1030)
    m.tick(1000 + 3700)  # the first two readings are over an hour old by now
    v = m.view()["runners"]["kokoro"]
    assert v["vram_gib"] == 1.0 and v["peak_gib"] == 1.0
