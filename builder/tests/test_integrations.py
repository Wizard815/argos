import json
import os
import shutil
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from src.command import parse_command
from src.integrations import llama_swap, odysseus
from src.runner import free_port

from .conftest import FIXTURES

COOKBOOK = os.path.join(FIXTURES, "cookbook_state.json")
SWAP = os.path.join(FIXTURES, "llama-swap.yaml")


def test_odysseus_cookbook():
    cmds = odysseus.load_cookbook_commands(COOKBOOK)
    assert [(c["kind"], c["name"], c["base_model"]) for c in cmds] == [
        ("task", "Qwen3 8B serve", "Qwen3-8B-GGUF"),
        ("preset", "Gemma 3 12B + vision", "gemma-3-12b-it-GGUF"),
    ]
    assert parse_command(cmds[1]["command"]).get("mmproj") == "/models/mmproj-gemma-3-12b-F16.gguf"


def test_odysseus_describe(tmp_path):
    assert odysseus.describe("") == {"configured": False}
    assert odysseus.describe(str(tmp_path / "nope.json"))["error"] == "file not found"
    bad = tmp_path / "bad.json"
    bad.write_text("[1, 2]")
    info = odysseus.describe(str(bad))
    assert info["ok"] is False and "not a JSON object" in info["error"]
    assert len(odysseus.describe(COOKBOOK)["commands"]) == 2


def test_llama_swap_models_expand_macros():
    cmds = {c["name"]: c["command"] for c in llama_swap.load_model_commands(SWAP)}
    assert set(cmds) == {"qwen3-30b-a3b", "llama-3.2-3b"}  # "broken" has no cmd
    moe = parse_command(cmds["qwen3-30b-a3b"])
    assert moe.env == {"CUDA_VISIBLE_DEVICES": "0"}
    assert moe.get("port") == "${PORT}"
    assert moe.get("ctx-size") == "32768"
    assert moe.get("n-cpu-moe") == "20"
    assert moe.summary()["cache_type_v"] == "q8_0"


def test_llama_swap_config_directory_merges(tmp_path):
    shutil.copy(SWAP, tmp_path / "a.yaml")
    (tmp_path / "b.yml").write_text('models:\n  "extra":\n    cmd: llama-server -m e.gguf --port ${PORT}\n')
    (tmp_path / "notes.txt").write_text("ignored")
    names = [c["name"] for c in llama_swap.load_model_commands(str(tmp_path))]
    assert sorted(names) == ["extra", "llama-3.2-3b", "qwen3-30b-a3b"]


def test_llama_swap_server_status():
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            body = b"OK" if self.path == "/health" else json.dumps(
                {"running": [{"model": "qwen", "state": "ready"}]}).encode()
            self.send_response(200)
            self.end_headers()
            self.wfile.write(body)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        status = llama_swap.server_status(f"http://127.0.0.1:{srv.server_port}")
        assert status == {"ok": True, "running": [{"model": "qwen", "state": "ready"}]}
    finally:
        srv.shutdown()
    down = llama_swap.server_status(f"http://127.0.0.1:{free_port()}", timeout=0.5)
    assert down["ok"] is False and down["error"]


def test_llama_swap_describe_reports_bad_config(tmp_path):
    bad = tmp_path / "c.yaml"
    bad.write_text("- just\n- a list\n")
    info = llama_swap.describe(str(bad), "")
    assert info["configured"] and info["config_ok"] is False
    assert llama_swap.describe("", "") == {"configured": False, "config": "", "url": ""}
