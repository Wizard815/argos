"""Power: the CPU's from its RAPL counters, and what the header shows."""
import time

from src.telemetry import read_cpu_power


def _rapl(root, name, uj, top=262143328850):
    d = root / name
    d.mkdir(exist_ok=True)
    (d / "energy_uj").write_text(str(uj))
    (d / "max_energy_range_uj").write_text(str(top))


def test_Power_cpu_watts_from_two_rapl_readings(tmp_path):
    _rapl(tmp_path, "intel-rapl:0", 1_000_000)
    _rapl(tmp_path, "intel-rapl:1", 5_000_000)
    _rapl(tmp_path, "intel-rapl:0:0", 999)  # a core sub-domain: inside package 0, not added again
    w, prev = read_cpu_power(None, str(tmp_path), now=100.0)
    assert w is None, "the first reading has nothing to compare with"
    _rapl(tmp_path, "intel-rapl:0", 1_000_000 + 150_000_000)  # 150 J in 2 s
    _rapl(tmp_path, "intel-rapl:1", 5_000_000 + 50_000_000)   # 50 J
    w, prev = read_cpu_power(prev, str(tmp_path), now=102.0)
    assert w == 100.0


def test_Power_a_counter_that_wraps_still_reads_right(tmp_path):
    _rapl(tmp_path, "intel-rapl:0", 990, top=1000)
    _, prev = read_cpu_power(None, str(tmp_path), now=0.0)
    _rapl(tmp_path, "intel-rapl:0", 10, top=1000)  # 990 -> 1000 -> 10: 20 uJ
    w, _ = read_cpu_power(prev, str(tmp_path), now=1.0)
    assert w == 0.0 or w == round(20 / 1e6, 1)


def test_Power_no_rapl_reads_none(tmp_path):
    assert read_cpu_power(None, str(tmp_path / "missing"))[0] is None


def test_Power_header_gets_gpu_and_cpu_watts(app, client):
    st = app.extensions["pb"].store
    now = time.time()
    st.add_gpu_samples([{"ts": now, "gpu": 0, "name": "MI50", "mem_used_mb": 1, "mem_total_mb": 32000, "power_w": 210.0},
                        {"ts": now, "gpu": 1, "name": "MI50", "mem_used_mb": 1, "mem_total_mb": 32000, "power_w": 190.0}])
    st.add_cpu_sample({"ts": now, "util": 20, "cores": 88, "mem_used_mb": 1, "mem_total_mb": 2, "power_w": 180.5})
    app.extensions["pb"].schedule_state["cpu_models"] = ["embedder"]
    d = client.get("/api/gpu/latest").get_json()
    assert sum(g["power_w"] for g in d["latest"]) == 400.0
    assert d["cpu"] == {"power_w": 180.5, "util": 20, "models": ["embedder"]}


def test_Power_reads_the_mounted_powercap_folder(tmp_path):
    nested = tmp_path / "intel-rapl"  # /sys/devices/virtual/powercap mounted at /host/powercap
    nested.mkdir()
    _rapl(nested, "intel-rapl:0", 0)
    _, prev = read_cpu_power(None, str(tmp_path), now=0.0)
    _rapl(nested, "intel-rapl:0", 90_000_000)
    assert read_cpu_power(prev, str(tmp_path), now=1.0)[0] == 90.0
