"""Profiles -> MoE offload: expert sizes read from the file, applied to every profile but the exempt ones."""
import struct

from src import gguf, moe
from src.command import parse_command

MIB = 2 ** 20


def _s(x):
    b = x.encode()
    return struct.pack("<Q", len(b)) + b


def fake_moe(path, layers=4, dense_first=1, expert=64 * MIB, other=8 * MIB):
    """A GGUF with only a header and a tensor table, whose data part has the right size."""
    tensors = [("token_embd.weight", other)]
    for i in range(layers):
        tensors.append((f"blk.{i}.attn_q.weight", MIB))
        if i >= dense_first:
            tensors += [(f"blk.{i}.ffn_up_exps.weight", expert // 2), (f"blk.{i}.ffn_down_exps.weight", expert // 2),
                        (f"blk.{i}.ffn_up_shexp.weight", MIB)]
        else:
            tensors.append((f"blk.{i}.ffn_up.weight", 2 * MIB))
    head = (b"GGUF" + struct.pack("<I", 3) + struct.pack("<Q", len(tensors)) + struct.pack("<Q", 1)
            + _s("general.architecture") + struct.pack("<I", 8) + _s("qwen3moe"))
    tab, off = b"", 0
    for n, sz in tensors:
        tab += _s(n) + struct.pack("<I", 1) + struct.pack("<Q", sz) + struct.pack("<I", 0) + struct.pack("<Q", off)
        off += sz
    body = head + tab
    with open(path, "wb") as f:
        f.write(body + b"\0" * ((32 - len(body) % 32) % 32))
        f.truncate(f.tell() + off)
    return str(path)


def test_MoE_the_file_says_how_much_each_layer_holds(tmp_path):
    m = gguf.read_moe(fake_moe(tmp_path / "m.gguf"))
    assert m["moe_layers"] == [1, 2, 3] and m["expert_bytes"][1] == 64 * MIB and m["layers"] == 4
    assert gguf.cpu_expert_bytes(m, 2) == 64 * MIB, "--n-cpu-moe 2 moves layers 0 and 1; layer 0 is dense"
    assert gguf.read_moe(str(tmp_path / "missing.gguf")) is None


def test_MoE_a_share_becomes_n_cpu_moe_and_skips_exempt_profiles(tmp_path):
    path = fake_moe(tmp_path / "m.gguf")
    m = gguf.read_moe(path)
    assert moe.layers_for(m, 0) == (0, False) and moe.layers_for(m, 34) == (2, False)
    assert moe.layers_for(m, 67) == (3, False) and moe.layers_for(m, 100) == (0, True)
    table = {path: {"pct": 67, "exempt": ["p2"]}}
    cmd = f"llama-server -m {path} -c 4096 --n-cpu-moe 1"
    got = parse_command(moe.apply(cmd, "p1", table))
    assert got.get("n-cpu-moe") == "3" and got.get("ctx-size") == "4096"
    assert moe.apply(cmd, "p2", table) == cmd, "exempt keeps its own"
    assert moe.apply(cmd, "p1", {path: {"pct": None}}) == cmd, "default: each profile's own"
    assert parse_command(moe.apply(cmd, "p1", {path: {"pct": 100}})).has("cpu-moe")


def test_MoE_the_estimate_counts_only_the_gpu_share(tmp_path):
    from src.schedule_rows import estimate_command
    path = fake_moe(tmp_path / "m.gguf")
    full = estimate_command(f"llama-server -m {path}")
    half = estimate_command(f"llama-server -m {path} --n-cpu-moe 3")
    assert half["cpu_gib"] == 0.12 and full["cpu_gib"] == 0
    assert half["weights_gib"] < full["weights_gib"] and half["gib"] < full["gib"]


def test_MoE_page_and_engine_state(client, app, tmp_path):
    path = fake_moe(tmp_path / "m.gguf")
    store = app.extensions["pb"].store
    for name in ("fast", "long"):
        r = client.post("/api/profiles", json={"name": name, "base_model": "m", "launch_command": f"llama-server -m {path} -c 8192"})
        assert r.status_code == 200, r.get_json()
    ids = {p["name"]: p["profile_id"] for p in store.list_profiles()}
    v = client.get("/api/moe").get_json()
    assert v["models"][0]["moe_layers"] == 3 and len(v["models"][0]["cum_gib"]) == 4
    r = client.put("/api/moe", json={"path": path, "pct": 100, "exempt": [ids["long"]]})
    assert r.status_code == 200 and r.get_json()["models"][0]["pct"] == 100
    state = client.get("/api/cookbook/state").get_json()
    cmds = {p["label"]: p["cmd"] for p in state["presets"]}
    assert "--cpu-moe" in cmds["fast"] and "cpu-moe" not in cmds["long"]
    assert "cpu-moe" not in store.list_profiles()[0]["launch_command"], "saved profiles are not changed"
    assert client.put("/api/moe", json={"path": path, "pct": 150}).status_code == 400
    client.put("/api/moe", json={"path": path, "pct": None})
    assert "cpu-moe" not in json_cmds(client)["fast"]


def json_cmds(client):
    return {p["label"]: p["cmd"] for p in client.get("/api/cookbook/state").get_json()["presets"]}


def test_MoE_default_remembers_the_slider_and_exempt_profiles(client, app, tmp_path):
    path = fake_moe(tmp_path / "m.gguf")
    for name in ("a", "b"):
        client.post("/api/profiles", json={"name": name, "base_model": "m", "launch_command": f"llama-server -m {path}"})
    ids = {p["name"]: p["profile_id"] for p in app.extensions["pb"].store.list_profiles()}
    client.put("/api/moe", json={"path": path, "pct": 67, "exempt": [ids["b"]]})
    m = client.put("/api/moe", json={"path": path, "pct": None, "exempt": [ids["b"]]}).get_json()["models"][0]
    assert m["pct"] is None and m["last_pct"] == 67 and [p["exempt"] for p in m["profiles"]].count(True) == 1
    assert all(p["runs_with"] == "all on the GPU" for p in m["profiles"]), "Default: nothing applied"
    m = client.put("/api/moe", json={"path": path, "pct": 67, "exempt": [ids["b"]]}).get_json()["models"][0]
    runs = {p["name"]: p["runs_with"] for p in m["profiles"]}
    assert runs == {"a": "--n-cpu-moe 3", "b": "all on the GPU"}, runs


def test_MoE_main_switch_and_a_value_saved_into_the_profile(client, app, tmp_path):
    from src import moe as moe_mod
    moe_mod.HOLD.served.clear()
    path = fake_moe(tmp_path / "m.gguf")
    for name in ("a", "b"):
        client.post("/api/profiles", json={"name": name, "base_model": "m", "launch_command": f"llama-server -m {path}"})
    store = app.extensions["pb"].store
    ids = {p["name"]: p["profile_id"] for p in store.list_profiles()}
    client.put("/api/moe", json={"path": path, "pct": 100, "exempt": []})
    r = client.put("/api/moe/profile", json={"profile_id": ids["b"], "pct": 34}).get_json()
    assert "--n-cpu-moe 2" in store.get_profile(ids["b"])["launch_command"], "saved into the profile itself"
    assert {p["name"]: p["own_pct"] for p in r["models"][0]["profiles"]}["b"] == 33
    cmds = lambda: {p["label"]: p["cmd"] for p in client.get("/api/cookbook/state").get_json()["presets"]}  # noqa: E731
    assert "--cpu-moe" in cmds()["a"] and "--cpu-moe" in cmds()["b"], "the model's setting is on top while the switch is on"
    off = client.put("/api/moe/enabled", json={"on": False}).get_json()
    assert off["enabled"] is False
    c = cmds()
    assert "moe" not in c["a"] and "--n-cpu-moe 2" in c["b"], "switch off: each runs with its own, b keeps its saved 2"
    client.put("/api/moe/enabled", json={"on": True})
    assert "--cpu-moe" in cmds()["a"]
    client.put("/api/moe/profile", json={"profile_id": ids["b"], "pct": 0})
    assert "moe" not in store.get_profile(ids["b"])["launch_command"]
    assert client.put("/api/moe/profile", json={"profile_id": "nope", "pct": 5}).status_code == 404


def test_MoE_a_busy_running_model_keeps_its_command_until_quiet():
    from src import moe as moe_mod
    h = moe_mod.Holder()
    old = [{"profile_id": "p", "name": "fast", "launch_command": "llama-server -m /m/x.gguf"}]
    new = [{"profile_id": "p", "name": "fast", "launch_command": "llama-server -m /m/x.gguf --n-cpu-moe 4"}]
    running = lambda: {"x--fast"}  # noqa: E731
    assert h.pick(old, old, running, lambda n: 1e9)[0]["launch_command"] == old[0]["launch_command"]
    got = h.pick(old, new, running, lambda n: 5.0)
    assert got[0]["launch_command"] == old[0]["launch_command"] and "p" in h.pending, "used 5 s ago: waits"
    got = h.pick(old, new, running, lambda n: 0.0)
    assert got[0]["launch_command"] == old[0]["launch_command"], "busy: waits"
    got = h.pick(old, new, running, lambda n: 31.0)
    assert got[0]["launch_command"] == new[0]["launch_command"] and not h.pending, "quiet 31 s: the new one goes out"
    assert h.take_released() == {"p": True} and h.take_released() == {}, "the reload is reported once, whoever let it go"
    h2 = moe_mod.Holder()
    h2.pick(old, old, lambda: set(), lambda n: 0.0)
    assert h2.pick(old, new, lambda: set(), lambda n: 0.0)[0]["launch_command"] == new[0]["launch_command"], "not running: at once"


def test_MoE_the_wait_before_a_reload_is_a_setting(client):
    from src import moe as moe_mod
    assert client.get("/api/moe").get_json()["idle_s"] == 30
    assert client.put("/api/moe/idle", json={"seconds": 90}).get_json()["idle_s"] == 90
    assert client.put("/api/moe/idle", json={"seconds": -1}).status_code == 400
    assert client.put("/api/moe/idle", json={"seconds": "x"}).status_code == 400
    assert client.put("/api/moe/idle", json={"seconds": 0}).get_json()["idle_s"] == 0
    h = moe_mod.Holder()
    old = [{"profile_id": "p", "name": "fast", "launch_command": "llama-server -m /m/x.gguf"}]
    new = [{"profile_id": "p", "name": "fast", "launch_command": "llama-server -m /m/x.gguf --n-cpu-moe 4"}]
    h.pick(old, old, lambda: {"x--fast"}, lambda n: 1e9)
    assert h.pick(old, new, lambda: {"x--fast"}, lambda n: 45.0, idle=90)[0] == old[0], "used 45 s ago, wait 90: waits"
    assert h.pick(old, new, lambda: {"x--fast"}, lambda n: 45.0, idle=40)[0] == new[0], "wait 40: goes"


def test_MoE_a_quiet_running_model_is_reloaded_with_its_new_command():
    from src import moe as moe_mod
    h = moe_mod.Holder()
    old = [{"profile_id": "p", "name": "fast", "launch_command": "llama-server -m /m/x.gguf"}]
    new = [{"profile_id": "p", "name": "fast", "launch_command": "llama-server -m /m/x.gguf --n-cpu-moe 4"}]
    h.pick(old, old, lambda: {"x--fast"}, lambda n: 1e9)
    h.pick(old, new, lambda: {"x--fast"}, lambda n: 60.0)
    assert h.take_released() == {"p": True}, "running and quiet: goes now, and is loaded again"
    h.pick(old, old, lambda: set(), lambda n: 0.0)
    assert h.take_released() == {}, "not running: nothing to load again"
