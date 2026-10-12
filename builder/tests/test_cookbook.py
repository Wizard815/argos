import json
import os
import re
import shlex

from src.command import parse_command, swap_model_id, to_odysseus
from src.cookbook import build_state, profile_presets

from .conftest import FIXTURES

ENV = re.compile(r"^[A-Z_][A-Z0-9_]*=")


def engine_accepts(cmd: str) -> tuple[str, list[str]]:
    """What Argos's internal/odysseus ParseCommand requires of a preset's cmd."""
    argv = shlex.split(re.sub(r"\\\s*\n\s*", " ", cmd))
    env = []
    while argv and ENV.match(argv[0]):
        env.append(argv.pop(0))
    assert argv[0] == "llama-server", argv[0]
    model = argv[argv.index("--model") + 1] if "--model" in argv else argv[argv.index("-m") + 1]
    assert model.lower().endswith(".gguf")
    assert "--port" in argv
    assert "$(" not in cmd and "`" not in cmd
    return swap_model_id(model), env


def test_odysseus_normal_form_matches_cookbook_shape():
    cmd = parse_command("HIP_VISIBLE_DEVICES=0 llama-server -c 16384 --jinja -ngl 99 -m '/app/models/a b/x.gguf' -lm dio")
    out = to_odysseus(cmd).to_string(style="odysseus")
    assert out == ('HIP_VISIBLE_DEVICES=0 llama-server --model "/app/models/a b/x.gguf" --host 0.0.0.0 '
                   '--port 8080 -ngl 99 -c 16384 --jinja -lm dio')
    assert parse_command(out).argv() == to_odysseus(cmd).argv()


def test_odysseus_form_leaves_wrappers_alone():
    wrapped = parse_command("python3 server.py --port 9000 -c 4096")
    assert to_odysseus(wrapped).argv() == wrapped.argv()
    custom = parse_command("/opt/llama/bin/llama-server-rocm -c 4096 -m /m/a.gguf")
    assert to_odysseus(custom).argv()[1:5] == ["--model", "/m/a.gguf", "--host", "0.0.0.0"]


def test_parses_what_odysseus_writes():
    raw = ("export HIP_VISIBLE_DEVICES=1\n"
           "llama-server --model \"$(printf %s '/app/models/q.gguf')\" --host 0.0.0.0 --port 8000 -ngl 99 -c 8192 \\\n"
           "  --flash-attn auto --cache-type-k q8_0 --cache-type-v q8_0")
    cmd = parse_command(raw)
    assert cmd.env == {"HIP_VISIBLE_DEVICES": "1"}
    assert cmd.get("model") == "/app/models/q.gguf"
    assert cmd.summary()["flash_attn"] == "auto"


def _profile(name, cmd, created=1.0, pid=None):
    return {"profile_id": pid or f"prof-{name}", "name": name, "base_model": "m", "launch_command": cmd,
            "created_at": created}


def test_presets_are_engine_compatible_and_unique_per_model():
    profiles = [
        _profile("fast", "llama-server -m /m/Qwen3-8B-Q4_K_M.gguf -ngl 99", 1),
        _profile("Fast!", "llama-server -m /m/Qwen3-8B-Q4_K_M.gguf -ngl 40", 2),
        _profile("fast", "CUDA_VISIBLE_DEVICES=1 llama-server -m /m/Other.gguf", 3),
        _profile("hf", "llama-server -hf org/repo:Q4_K_M", 4),
    ]
    presets, warnings = profile_presets(profiles)
    assert [p["label"] for p in presets] == ["fast", "Fast! 2", "fast"]
    assert "not a .gguf path" in warnings[0]
    ids = [(engine_accepts(p["cmd"])[0], p["label"]) for p in presets]
    assert ids[0][0] == ids[1][0] == "qwen3-8b-q4_k_m"
    assert engine_accepts(presets[2]["cmd"])[1] == ["CUDA_VISIBLE_DEVICES=1"]
    for p in presets:
        assert set(p) >= {"name", "model", "cmd", "remoteHost", "port", "label", "fields"}
        assert p["fields"]["_manual_cmd"] == p["cmd"]


def test_no_cap_on_presets_per_model():
    profiles = [_profile(f"cfg {i}", f"llama-server -m /m/one.gguf -c {1024 * (i + 1)}", i) for i in range(12)]
    presets, _ = profile_presets(profiles)
    assert len(presets) == 12  # the Cookbook stops at five


ODY = {"presets": [
    {"name": "Gemma 3 12B + vision", "model": "unsloth/gemma-3-12b-it-GGUF", "host": "root@mx",
     "cmd": "llama-server -m /models/gemma-3-12b-it-Q4_K_M.gguf --mmproj /models/mmproj-gemma-3-12b-F16.gguf -c 8192 -ngl 99 --port 8081", "port": 8081},
    {"name": "no command", "model": "x/y"},
]}


def test_state_passes_odysseus_through_and_avoids_its_labels():
    profiles = [_profile("Gemma 3 12B + vision", "llama-server -m /models/gemma-3-12b-it-Q4_K_M.gguf")]
    state = build_state(profiles, [], ODY)
    assert [p["label"] for p in state["presets"]] == ["Gemma 3 12B + vision", "Gemma 3 12B + vision 2"]
    theirs = state["presets"][0]
    assert theirs["remoteHost"] == "root@mx" and theirs["port"] == "8081", "Odysseus' host and label names are mapped"
    assert state["tasks"] == []
    own_only = build_state(profiles, [], None)
    assert [p["label"] for p in own_only["presets"]] == ["Gemma 3 12B + vision"]
    broken = build_state(profiles, [], {"presets": [], "error": "not reachable"})
    assert broken["warnings"] == ["odysseus presets: not reachable"] and len(broken["presets"]) == 1


def test_state_endpoint_token_comes_from_the_environment(client, monkeypatch):
    client.post("/api/profiles", json={"name": "a", "launch_command": "llama-server -m /m/a.gguf"})
    state = client.get("/api/cookbook/state").get_json()
    assert "warnings" not in state and [p["label"] for p in state["presets"]][-1] == "a"
    monkeypatch.setenv("ARGOS_COOKBOOK_TOKEN", "s3cret")
    assert client.get("/api/cookbook/state").status_code == 401
    r = client.get("/api/cookbook/state", headers={"X-Odysseus-Internal-Token": "s3cret"})
    assert r.status_code == 200 and r.get_json()["presets"]
