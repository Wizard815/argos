"""Profiles -> Power: a power cap and top clock per profile, applied while it runs; watts and tokens per watt for
benchmarks. The GPUs here are fake sysfs folders; the real cards need the hardware (see the PR)."""
import json
import time

import pytest

from src import gpu_power as gp


def fake_card(root, n, pci, cap=225, lo=50, hi=300, default=225, junction=60.0, perf="auto"):
    """/sys/class/drm/cardN/device for an MI50, as amdgpu lays it out."""
    dev = root / f"card{n}" / "device"
    hw = dev / "hwmon" / f"hwmon{n}"
    hw.mkdir(parents=True)
    (dev / "vendor").write_text("0x1002\n")
    (dev / "device").write_text("0x66a1\n")
    (dev / "uevent").write_text(f"DRIVER=amdgpu\nPCI_SLOT_NAME={pci}\n")
    (dev / "pp_dpm_sclk").write_text("0: 925Mhz\n1: 1138Mhz\n2: 1269Mhz\n3: 1348Mhz\n4: 1440Mhz\n5: 1528Mhz\n6: 1600Mhz\n7: 1683Mhz\n8: 1725Mhz *\n")
    (dev / "power_dpm_force_performance_level").write_text(perf + "\n")
    for name, w in (("power1_cap", cap), ("power1_cap_min", lo), ("power1_cap_max", hi), ("power1_cap_default", default)):
        (hw / name).write_text(str(int(w * 1e6)))
    (hw / "power1_average").write_text(str(int(180e6)))
    for i, (label, c) in enumerate((("edge", 50.0), ("junction", junction), ("mem", 70.0)), start=1):
        (hw / f"temp{i}_label").write_text(label)
        (hw / f"temp{i}_input").write_text(str(int(c * 1000)))
    (root / f"card{n}-DP-1").mkdir()  # a connector: skipped
    return dev, hw


def _cap(hw):
    return int((hw / "power1_cap").read_text()) / 1e6


def test_Power_reads_each_amd_cards_cap_range_clocks_and_temps(tmp_path):
    fake_card(tmp_path, 1, "0000:03:00.0", cap=178, default=178)
    fake_card(tmp_path, 2, "0000:04:00.0", junction=88.5)
    cards = gp.read_sysfs(str(tmp_path))
    assert [c["pci"] for c in cards] == ["0000:03:00.0", "0000:04:00.0"]
    a = cards[0]
    assert (a["cap_w"], a["cap_min_w"], a["cap_max_w"], a["cap_default_w"]) == (178, 50, 300, 178)
    assert a["sclk_levels_mhz"][0] == 925 and a["sclk_levels_mhz"][-1] == 1725 and a["sclk_mhz"] == 1725
    assert cards[1]["junction_c"] == 88.5 and a["power_w"] == 180 and not a["sclk_limited"]
    assert gp.hottest(cards) == 88.5


def test_Power_settings_are_checked_and_labelled():
    assert gp.clean({"cap_w": "180", "sclk_mhz": ""}) == {"on": True, "cap_w": 180, "sclk_mhz": None}
    assert gp.clean({"on": False, "cap_w": 150, "sclk_mhz": 1400}) == {"on": False, "cap_w": 150, "sclk_mhz": 1400}
    with pytest.raises(ValueError):
        gp.clean({"cap_w": 20})
    with pytest.raises(ValueError):
        gp.clean({"cap_w": "lots"})
    assert gp.label({"cap_w": 180}) == "180 W" and gp.label({"cap_w": None}) == "stock"
    assert gp.label({"cap_w": 150, "sclk_mhz": 1400}) == "150 W, ≤1400 MHz"


def test_Power_several_running_profiles_get_the_most_generous_setting(tmp_path):
    fake_card(tmp_path, 1, "a", default=225)
    cards = gp.read_sysfs(str(tmp_path))
    assert gp.plan([{"cap_w": 150, "sclk_mhz": 1400}, {"cap_w": 180, "sclk_mhz": 1300}], cards)["a"] == {"cap_w": 180, "sclk_mhz": 1400}
    assert gp.plan([{"cap_w": 150, "sclk_mhz": 1400}, {"cap_w": None, "sclk_mhz": None}], cards)["a"] == {"cap_w": None, "sclk_mhz": None}, \
        "stock (225 W) is above 150 W, and no clock limit beats one"
    assert gp.plan([{"cap_w": 250}, {"cap_w": None}], cards)["a"]["cap_w"] == 250, "a cap above stock wins over stock"


def test_Power_sysfs_writes_the_cap_and_masks_the_clock_levels(tmp_path):
    dev, hw = fake_card(tmp_path, 1, "a")
    b = gp.SysfsBackend(str(tmp_path))
    assert b.apply({"a": {"cap_w": 150, "sclk_mhz": 1450}}) == []
    assert _cap(hw) == 150
    assert (dev / "power_dpm_force_performance_level").read_text() == "manual"
    assert (dev / "pp_dpm_sclk").read_text() == "0 1 2 3 4", "levels up to 1440 MHz"
    card = b.read()["cards"][0]
    assert card["sclk_limited"] and card["sclk_limit_mhz"] == 1440
    assert b.apply({"a": {"cap_w": None, "sclk_mhz": None}}) == []
    assert _cap(hw) == 225 and (dev / "power_dpm_force_performance_level").read_text() == "auto", "stock: the default cap, DPM on auto"
    errs = b.apply({"a": {"cap_w": 350}})
    assert errs and "outside" in errs[0] and _cap(hw) == 225, "never past the card's own maximum"


def test_Power_a_raise_waits_while_a_gpu_is_hot_a_cut_never_does(tmp_path):
    fake_card(tmp_path, 1, "a", cap=150, junction=97.0)
    cards = gp.read_sysfs(str(tmp_path))
    todo, held = gp.decide({"a": {"cap_w": 200, "sclk_mhz": None}}, cards, guard_c=95)
    assert todo == {} and "97" in held[0]
    todo, held = gp.decide({"a": {"cap_w": 125, "sclk_mhz": None}}, cards, guard_c=95)
    assert todo == {"a": {"cap_w": 125, "sclk_mhz": None}} and held == []
    todo, _ = gp.decide({"a": {"cap_w": 150, "sclk_mhz": None}}, cards, guard_c=95)
    assert todo == {}, "already there: nothing written"


class FakeStore:
    def __init__(self):
        self.s = {}

    def get_settings(self):
        return self.s

    def set_setting(self, k, v):
        self.s[k] = v


def test_Power_governor_follows_what_runs_then_idle_then_stock(tmp_path):
    _, hw = fake_card(tmp_path, 1, "a")
    store, g = FakeStore(), gp.Governor()
    g._backend_key, g._backend = "x", gp.SysfsBackend(str(tmp_path))
    g.backend = lambda cfg: g._backend
    cfg = {"guard_c": 95}
    assert g.tick(store, cfg, ["p1"])["applied"] == {} and _cap(hw) == 225, "nothing set: the card is left alone"
    store.s[gp.KEY] = {"p1": {"on": True, "cap_w": 150, "sclk_mhz": None}, "p2": {"on": False, "cap_w": 125, "sclk_mhz": None}}
    g.tick(store, cfg, ["p1"])
    assert _cap(hw) == 150 and g.touched
    g.tick(store, cfg, ["p2"])  # p2 is switched off (its 125 W kept): nothing asks, Argos changed it, so stock
    assert _cap(hw) == 225 and not g.touched
    store.s[gp.IDLE_KEY] = {"on": True, "cap_w": 125, "sclk_mhz": None}
    g.tick(store, cfg, [])
    assert _cap(hw) == 125
    g.hold = {"on": True, "cap_w": 200, "sclk_mhz": None}  # a benchmark comparing 200 W
    assert "benchmark" in g.tick(store, cfg, ["p1"])["why"] and _cap(hw) == 200
    g.hold = None
    store.s[gp.ENABLED_KEY] = False
    g.tick(store, cfg, ["p1"])
    assert _cap(hw) == 225, "switched off: back to stock once"
    (hw / "power1_cap").write_text(str(int(160e6)))  # someone else sets it
    g.tick(store, cfg, ["p1"])
    assert _cap(hw) == 160, "then left alone"


def test_Power_rocm_smi_backend_finds_the_index_by_pci_and_sets_cap_and_clocks(tmp_path):
    fake_card(tmp_path, 1, "0000:03:00.0")
    calls = []

    def run(argv):
        calls.append(argv)
        return json.dumps({"card0": {"PCI Bus": "0000:03:00.0"}}) if "--showbus" in argv else ""
    b = gp.SmiBackend("/opt/rocm/bin/rocm-smi", run=run, root=str(tmp_path))
    assert b.apply({"0000:03:00.0": {"cap_w": 180, "sclk_mhz": 1300}}) == []
    flat = [" ".join(a[1:]) for a in calls]
    assert "-d 0 --autorespond y --setpoweroverdrive 180" in flat
    assert "-d 0 --autorespond y --setsclk 0 1 2" in flat


def test_Power_fan_bridge_backend_and_fan_state():
    seen = []

    def http(method, path, payload=None):
        seen.append((method, path, payload))
        if path == "/api/state":
            return {"state": {"phase": "boost", "floor_pct": 40, "control_c": 81.0,
                              "ilo": {"fans": [{"pct": 38}, {"pct": 42}]},
                              "gpus": [{"card": "card1", "pci": "a", "junction_c": 81.0, "edge_c": 60.0, "memory_c": 74.0, "power_w": 190.0}]}}
        if method == "GET":
            return {"cards": [{"pci": "a", "cap_w": 225}], "fans": {}}
        return {"ok": True, "errors": []}
    b = gp.FanBridgeBackend("http://fan-bridge:5000/", "t", http=http)
    assert b.read()["cards"][0]["cap_w"] == 225
    assert b.apply({"a": {"cap_w": 150, "sclk_mhz": None}}) == []
    assert seen[-1] == ("POST", "/api/power", {"cards": {"a": {"cap_w": 150, "sclk_mhz": None}}})
    f = gp.fan_state("http://fan-bridge:5000", http=http)
    assert f["phase"] == "boost" and f["fan_avg_pct"] == 40 and f["gpus"][0]["junction_c"] == 81.0
    assert "error" in gp.fan_state("http://127.0.0.1:9")


def test_Power_auto_backend_order():
    assert gp.pick_backend({"backend": "auto", "fan_bridge_url": "http://fb:5000"}).name == "fan-bridge"
    assert gp.pick_backend({"backend": "fan-bridge"}) is None, "fan-bridge without a URL"
    assert gp.pick_backend({"backend": "rocm-smi"}, which=lambda x: "/usr/bin/rocm-smi").name == "rocm-smi"
    assert gp.pick_backend({"backend": "amd-smi"}, which=lambda x: None) is None


def test_Power_watts_count_only_busy_gpus_and_busy_readings():
    s = [
        {"ts": 1.0, "gpu": 0, "util": 2, "power_w": 20, "temp_c": 40},   # still loading: left out
        {"ts": 1.0, "gpu": 1, "util": 0, "power_w": 18, "temp_c": 38},
        {"ts": 3.0, "gpu": 0, "util": 95, "power_w": 200, "temp_c": 70},
        {"ts": 3.0, "gpu": 1, "util": 0, "power_w": 19, "temp_c": 39},  # the idle card beside it: left out
        {"ts": 5.0, "gpu": 0, "util": 90, "power_w": 180, "temp_c": 72},
    ]
    assert gp.watts_between(s) == (190.0, 72)
    assert gp.watts_between([]) == (None, None)


def test_Power_benchmark_rows_get_watts_and_tokens_per_watt():
    rows = [{"tokens_per_second": 40.0, "prompt_per_second": None, "note": "tg128"},
            {"tokens_per_second": None, "prompt_per_second": 400.0, "note": "pp512"}]
    results = [{"t_start": 0, "t_end": 10}, {"t_start": 10, "t_end": 11}]

    def samples(t0, t1):
        if (t0, t1) == (0, 10):
            return [{"ts": 5, "gpu": 0, "util": 99, "power_w": 160.0, "temp_c": 71}]
        if (t0, t1) == (10, 11):
            return []  # shorter than a reading
        return [{"ts": 5, "gpu": 0, "util": 99, "power_w": 200.0, "temp_c": 75}]
    gp.add_power(rows, results, 0, 11, samples, "150 W")
    assert rows[0]["power_w"] == 160 and rows[0]["tokens_per_watt"] == 0.25 and rows[0]["power_label"] == "150 W"
    assert rows[1]["power_w"] == 200 and rows[1]["prompt_per_watt"] == 2.0, "the whole run's average when a test is too short"
    assert "160 W (150 W)" in rows[0]["note"] and rows[0]["temp_c"] == 71


def test_Power_compare_puts_a_profiles_settings_side_by_side():
    runs = [
        {"profile_name": "fast", "power_label": "stock", "tokens_per_second": 50, "power_w": 220, "tokens_per_watt": 0.227, "created_at": 1},
        {"profile_name": "fast", "power_label": "150 W", "tokens_per_second": 48, "power_w": 150, "tokens_per_watt": 0.32, "created_at": 2},
        {"profile_name": "fast", "power_label": "150 W", "prompt_per_second": 300, "power_w": 150, "prompt_per_watt": 2.0, "created_at": 3},
        {"profile_name": "fast", "power_label": "", "tokens_per_second": 10},  # no watts: not compared
    ]
    got = gp.compare(runs)
    assert [g["power"] for g in got] == ["150 W", "stock"], "most tokens per watt first"
    assert got[0]["tg"] == 48 and got[0]["pp"] == 300 and got[0]["runs"] == 2 and got[0]["pp_per_w"] == 2.0


def test_Power_page_saves_and_remembers_each_profiles_setting(client, app):
    r = client.post("/api/profiles", json={"name": "fast", "base_model": "m", "launch_command": "llama-server -m /m/m.gguf"})
    assert r.status_code == 200, r.get_json()
    pid = app.extensions["pb"].store.list_profiles()[0]["profile_id"]
    v = client.get("/api/power").get_json()
    assert v["enabled"] and v["profiles"][0]["on"] is False and [p["id"] for p in v["presets"]][0] == "stock"
    r = client.put("/api/power/profile", json={"profile_id": pid, "on": True, "cap_w": 150, "sclk_mhz": 1400})
    assert r.status_code == 200 and r.get_json()["profiles"][0]["label"] == "150 W, ≤1400 MHz"
    r = client.put("/api/power/profile", json={"profile_id": pid, "on": False, "cap_w": 150, "sclk_mhz": 1400})
    p = r.get_json()["profiles"][0]
    assert p["on"] is False and (p["cap_w"], p["sclk_mhz"]) == (150, 1400), "switched off, the values are kept"
    assert client.put("/api/power/profile", json={"profile_id": pid, "cap_w": 5}).status_code == 400
    assert client.put("/api/power/profile", json={"profile_id": "nope", "cap_w": 150}).status_code == 404
    assert client.put("/api/power/idle", json={"on": True, "cap_w": 125}).get_json()["idle"]["cap_w"] == 125
    assert client.put("/api/power/enabled", json={"on": False}).get_json()["enabled"] is False
    events = app.extensions["pb"].store.list_events(kind="power")
    assert any("150 W" in e["message"] for e in events)


def test_Power_saved_benchmark_has_watts_from_the_gpu_readings(app):
    from src.bench import BenchRun
    pb = app.extensions["pb"]
    now = time.time()
    pb.store.add_gpu_samples([{"ts": now + 1, "gpu": 0, "name": "MI50", "util": 99, "power_w": 170.0, "temp_c": 70,
                               "mem_used_mb": 1, "mem_total_mb": 32000}])
    run = BenchRun(bench_id="bench-power", argv=["llama-bench"], env={}, meta={"profile_name": "fast", "power": {"label": "180 W"}},
                   started_at=now, ended_at=now + 3, exit_code=0,
                   results=[{"avg_ts": 34.0, "stddev_ts": 0.1, "n_prompt": 0, "n_gen": 128, "kind": "tg", "test": "tg128",
                             "t_start": now, "t_end": now + 3}])
    pb.benches.on_done(run)
    row = pb.store.list_runs()[0]
    assert row["power_w"] == 170 and row["tokens_per_watt"] == 0.2 and row["power_label"] == "180 W"
