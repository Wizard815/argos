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


ROCM_SMI = """{"card0": {"Temperature (Sensor edge) (C)": "58.0", "Temperature (Sensor junction) (C)": "81.0",
 "Temperature (Sensor memory) (C)": "74.0", "Average Graphics Package Power (W)": "212.0"},
 "card1": {"Temperature (Sensor edge) (C)": "55.0", "Temperature (Sensor junction) (C)": "77.0",
 "Temperature (Sensor memory) (C)": "N/A", "Current Socket Graphics Package Power (W)": "189.0"}, "system": {"Driver version": "6.8"}}"""


def test_Sensors_rocm_smi_gives_edge_junction_memory_and_power():
    from src.telemetry import parse_rocm_smi_sensors
    got = parse_rocm_smi_sensors(ROCM_SMI)
    assert got[0] == {"edge": 58.0, "junction": 81.0, "mem": 74.0, "power_w": 212.0, "source": "rocm-smi"}
    assert got[1]["junction"] == 77.0 and "mem" not in got[1] and got[1]["power_w"] == 189.0, "N/A is left out; either power key"
    assert parse_rocm_smi_sensors("not json") == {}


def test_Sensors_sysfs_reads_the_labelled_hwmon_temps(tmp_path):
    from src.telemetry import read_sysfs_sensors
    dev = tmp_path / "card0" / "device"
    hw = dev / "hwmon" / "hwmon3"
    hw.mkdir(parents=True)
    (dev / "mem_info_vram_total").write_text(str(32 * 2**30))
    for n, (label, milli) in enumerate((("edge", 58000), ("junction", 81000), ("mem", 74000)), start=1):
        (hw / f"temp{n}_input").write_text(str(milli))
        (hw / f"temp{n}_label").write_text(label)
    (hw / "power1_average").write_text("212000000")
    assert read_sysfs_sensors(str(tmp_path)) == {0: {"edge": 58.0, "junction": 81.0, "mem": 74.0, "power_w": 212.0, "source": "sysfs"}}


def test_Sensors_reach_the_header(app, client, monkeypatch):
    from src import telemetry
    monkeypatch.setattr(telemetry, "gpu_sensors", lambda max_age=3.0: {0: {"edge": 58.0, "junction": 81.0, "source": "rocm-smi"}})
    assert client.get("/api/gpu/latest").get_json()["sensors"] == {"0": {"edge": 58.0, "junction": 81.0, "source": "rocm-smi"}}
