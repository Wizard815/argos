"""Playground -> llama-bench: the command a profile implies, where it runs, its JSON lines, and the saved results."""
import stat
import time

from src.bench import build, params_from_command, parse_line, program_for
from src.command import parse_command

PROFILE = ("HIP_VISIBLE_DEVICES=0,1 /app/llama-server -m /app/models/t.gguf --port ${PORT} -ngl 99 -fa on "
           "-ctk q8_0 -ctv q8_0 -sm layer -ub 512 -t 44 -c 524288")


def test_Bench_a_profile_fills_the_settings_it_implies():
    p = params_from_command(PROFILE)
    assert p["model"] == "/app/models/t.gguf" and p["n_gpu_layers"] == "99" and p["flash_attn"] == "1"
    assert p["cache_type_k"] == "q8_0" and p["split_mode"] == "layer" and p["ubatch_size"] == "512"
    assert p["env"] == {"HIP_VISIBLE_DEVICES": "0,1"}
    assert "ctx_size" not in p, "llama-bench takes depths, not a context size"


def test_Bench_runs_where_the_model_runs():
    assert program_for(parse_command(PROFILE)) == ["/app/llama-bench"], "beside llama-server"
    assert program_for(parse_command("llama-server -m x.gguf")) == ["llama-bench"]
    remote = parse_command("/app/bin/remote-llama rocm_srv3 -m x.gguf --port 1")
    assert program_for(remote) == ["/app/bin/remote-llama", "rocm_srv3", "--bench"], "over SSH, no port"
    argv, env = build({"model": "/m/x.gguf", "depth": "0, 16384", "flash_attn": "on,off", "env": {"HIP_VISIBLE_DEVICES": "1"}},
                      "llama-server -m x.gguf", {"program": ["/app/bin/remote-llama", "rocm_srv3"], "env": {"ROCM_SRV3_SSH_HOST": "h"}})
    assert argv[:3] == ["/app/bin/remote-llama", "rocm_srv3", "--bench"]
    assert argv[argv.index("-d") + 1] == "0,16384" and argv[argv.index("-fa") + 1] == "1,0" and argv[-2:] == ["-o", "jsonl"]
    assert env == {"ROCM_SRV3_SSH_HOST": "h", "HIP_VISIBLE_DEVICES": "1"}


def test_Bench_reads_its_json_lines_among_the_log():
    assert parse_line("load_backend: loaded ROCm backend") is None
    r = parse_line('{"n_prompt": 0, "n_gen": 128, "n_depth": 65536, "avg_ts": 20.1, "stddev_ts": 0.4}\r\n')
    assert r["test"] == "tg128 @ d65536" and r["kind"] == "tg"
    assert parse_line('{"n_prompt": 4096, "n_gen": 128, "n_depth": 0, "avg_ts": 300}')["test"] == "pp4096+tg128"


def test_Bench_a_run_from_a_profile_ends_up_in_benchmarks(app, client, tmp_path):
    fake = tmp_path / "llama-bench"
    fake.write_text("#!/usr/bin/env python3\nimport json\nprint('loading')\n"
                    "print(json.dumps({'n_prompt': 512, 'n_gen': 0, 'n_depth': 0, 'avg_ts': 900.0, 'stddev_ts': 9, 'n_ubatch': 512}))\n"
                    "print(json.dumps({'n_prompt': 0, 'n_gen': 128, 'n_depth': 0, 'avg_ts': 42.0, 'stddev_ts': 0.4, 'n_ubatch': 512}))\n")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    pid = client.post("/api/profiles", json={"name": "t", "base_model": "t",
                                             "launch_command": f"{tmp_path}/llama-server -m /m/t.gguf --port ${{PORT}} -ngl 99"}).get_json()["profile"]["profile_id"]
    d = client.get(f"/api/bench/defaults?profile_id={pid}").get_json()
    assert d["params"]["model"] == "/m/t.gguf"
    j = client.post("/api/bench", json={"profile_id": pid, "params": d["params"], "server_id": "local"}).get_json()
    bid = j["bench"]["bench_id"]
    for _ in range(100):
        run = client.get(f"/api/bench/{bid}").get_json()["bench"]
        if not run["running"] and run["saved"]:
            break
        time.sleep(0.05)
    assert [r["test"] for r in run["results"]] == ["pp512", "tg128"] and "loading" in run["output"]
    notes = [r["note"] for r in client.get("/api/runs?limit=10").get_json()["runs"]]
    assert any("llama-bench pp512: 900.00" in n for n in notes) and any("tg128: 42.00" in n for n in notes)
    assert client.post(f"/api/bench/{bid}/stop").status_code == 404, "not running any more"
