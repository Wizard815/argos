import json
import os
import time

from src.assistant import pick_quant

from .conftest import fake_command
from .fakes import make_hub, serve


def sse(resp) -> list[dict]:
    return [json.loads(line[5:]) for line in resp.get_data(as_text=True).splitlines() if line.startswith("data:")]


def use_fake_model(client, app, **assistant):
    p = client.post("/api/profiles", json={"name": "fake assistant",
                                           "launch_command": fake_command()}).get_json()["profile"]
    client.put("/api/settings/assistant", json={"mode": "managed", "profile_id": p["profile_id"], **assistant})
    return p


def test_pick_quant():
    qs = [{"quant": "F16", "size": 9}, {"quant": "Q4_K_M", "size": 4}, {"quant": "Q8_0", "size": 6},
          {"quant": "IQ2_XS", "size": 2}]
    assert pick_quant(qs)["quant"] == "Q8_0"
    assert pick_quant([q for q in qs if q["quant"] != "Q8_0"])["quant"] == "Q4_K_M"
    assert pick_quant([{"quant": "F16", "size": 1}]) is None


def test_chat_needs_a_model(client):
    events = sse(client.post("/api/assistant/chat", json={"message": "hi"}))
    assert events[-1]["type"] == "error" and "Set up Qwen3-0.6B" in events[-1]["error"]


def test_managed_chat_digest_reset(client, app):
    store = app.extensions["pb"].store
    store.add_gpu_samples([{"ts": time.time(), "gpu": 0, "name": "MI50", "util": 91, "mem_used_mb": 30000,
                            "mem_total_mb": 32768, "temp_c": 77, "power_w": 240, "source": "test"}])
    store.add_requests([{"source_id": 1, "ts": time.time(), "model": "qwen3.6-35b", "tokens_per_second": 42.0,
                         "prompt_per_second": 700.0, "status": 200}])
    use_fake_model(client, app)

    digest = client.get("/api/assistant/digest").get_json()["digest"]
    assert "GPU0 MI50: 91% busy, VRAM 29.3/32.0 GB, 77°C, 240 W" in digest
    assert "qwen3.6-35b: 1 req, gen 42.0 t/s avg" in digest

    events = sse(client.post("/api/assistant/chat", json={"message": "how is qwen3.6-35b doing?"}))
    kinds = [e["type"] for e in events]
    assert kinds[0] == "status" and kinds[-1] == "done", events
    answer = "".join(e["text"] for e in events if e["type"] == "delta")
    assert answer == "You said: how is qwen3.6-35b doing?. history=1 live=yes"

    events = sse(client.post("/api/assistant/chat", json={"message": "think first"}))
    answer = "".join(e["text"] for e in events if e["type"] == "delta")
    assert "<think>" not in answer and answer.startswith("You said: think first. history=3")

    state = client.get("/api/assistant").get_json()
    assert [m["role"] for m in state["messages"]] == ["user", "assistant"] * 2
    assert state["server"]["running"] and state["server"]["ready"]
    assert client.post("/api/assistant/reset").get_json()["ok"]
    assert client.get("/api/assistant").get_json()["messages"] == []
    assert any(e["message"] == "chat reset" for e in client.get("/api/events?kind=assistant").get_json()["events"])
    client.post("/api/assistant/stop")
    assert client.get("/api/assistant").get_json()["server"]["running"] is False


def test_compaction_keeps_recent_turns(client, app):
    use_fake_model(client, app, context_tokens=1400, keep_recent=2)
    long = "x" * 900
    compacted = False
    for i in range(4):
        events = sse(client.post("/api/assistant/chat", json={"message": f"q{i} {long}"}))
        assert events[-1]["type"] == "done", events
        compacted |= any(e["type"] == "compacted" for e in events)
    assert compacted
    state = client.get("/api/assistant").get_json()
    assert state["summary"].startswith("SUMMARY of")
    live = [m for m in state["messages"] if not m["compacted"]]
    assert len(live) <= 3 and len(state["messages"]) == 8  # nothing deleted, only marked
    events = sse(client.post("/api/assistant/chat", json={"message": "and now?"}))
    assert "summary=yes" in "".join(e["text"] for e in events if e["type"] == "delta")


def test_endpoint_mode(client, app):
    runner = app.extensions["runner"]
    run = runner.launch(fake_command())
    deadline = time.time() + 15
    while not runner.health(run) and time.time() < deadline:
        time.sleep(0.1)
    client.put("/api/settings/assistant", json={"mode": "endpoint", "endpoint": f"http://127.0.0.1:{run.port}/v1",
                                                "model": "qwen3-0.6b-q8_0"})
    events = sse(client.post("/api/assistant/chat", json={"message": "ping"}))
    assert events[-1]["type"] == "done" and not any(e["type"] == "status" for e in events)
    assert client.get("/api/assistant").get_json()["server"]["mode"] == "endpoint"


def test_setup_uses_local_file_or_downloads(client, app):
    pb = app.extensions["pb"]
    models_dir = pb.settings.get("paths")["models_dir"]
    path = os.path.join(models_dir, "Qwen3-0.6B-GGUF", "Qwen3-0.6B-Q8_0.gguf")
    os.makedirs(os.path.dirname(path))
    open(path, "wb").write(b"x")
    client.put("/api/settings/prefab", json={"load_mode": "dio", "split_mode": "layer", "env": "HIP_VISIBLE_DEVICES=0"})
    r = client.post("/api/assistant/setup").get_json()
    assert r["download"] is None
    cmd = r["profile"]["launch_command"]
    assert cmd == (f'HIP_VISIBLE_DEVICES=0 llama-server --model "{path}" --host 127.0.0.1 --port ${{PORT}} '
                   '-ngl 99 -c 8192 --flash-attn on -lm dio --split-mode none --jinja')
    assert pb.settings.get("assistant")["profile_id"] == r["profile"]["profile_id"]
    again = client.post("/api/assistant/setup").get_json()["profile"]
    assert again["profile_id"] == r["profile"]["profile_id"]  # updated, not duplicated

    os.remove(path)
    with serve(make_hub({"Qwen3-0.6B-Q8_0.gguf": b"q" * 64, "Qwen3-0.6B-F16.gguf": b"f" * 128})) as url:
        client.put("/api/settings/downloads", json={"hf_endpoint": url})
        r = client.post("/api/assistant/setup").get_json()
        assert r["download"]["files"] == [{"name": "Qwen3-0.6B-Q8_0.gguf", "size": 64}]
        deadline = time.time() + 10
        while not os.path.exists(path) and time.time() < deadline:
            time.sleep(0.05)
        assert os.path.getsize(path) == 64
