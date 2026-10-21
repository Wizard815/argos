import threading

from src.cookbook import _label_key, build_state

LONG = "Qwen3.6 35B A3B UD Q4_K_M dual GPU 256K context with flash attention"
CMD = "llama-server -m /models/Qwen3.6-35B-A3B-UD-Q4_K_M.gguf -c 32768"


def _run(profiles, odysseus=None):
    out = {}
    t = threading.Thread(target=lambda: out.update(state=build_state(profiles, [], odysseus)), daemon=True)
    t.start()
    t.join(5)
    assert not t.is_alive(), "build_state never finished"
    return out["state"]


def test_long_names_that_collide_still_get_unique_labels():
    # two profiles named alike past the 40-character cut: "name 2" cut to 40 is "name" again, which looped forever
    profiles = [{"profile_id": f"p{i}", "name": LONG, "launch_command": CMD, "created_at": i} for i in range(3)]
    keys = [_label_key(p["label"]) for p in _run(profiles)["presets"]]
    assert len(keys) == 3 and len(set(keys)) == 3 and all(0 < len(k) <= 40 for k in keys)


def test_long_name_taken_by_an_odysseus_preset():
    ody = {"presets": [{"label": LONG, "cmd": CMD}], "error": ""}
    state = _run([{"profile_id": "p", "name": LONG, "launch_command": CMD, "created_at": 1}], ody)
    keys = [_label_key(p["label"]) for p in state["presets"]]
    assert len(set(keys)) == 2


def test_short_names_keep_their_usual_suffix():
    profiles = [{"profile_id": f"p{i}", "name": "fast", "launch_command": CMD, "created_at": i} for i in range(2)]
    assert [p["label"] for p in _run(profiles)["presets"]] == ["fast", "fast 2"]
