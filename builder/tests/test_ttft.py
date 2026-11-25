"""Time to first token: measured loads and prompt speeds win over the estimate; the settings that change it."""
import time

from src.library import model_id_for_command
from src.ttft import ttft_estimate


def _model(tmp_path, gb=2):
    p = tmp_path / "m-Q8_0.gguf"
    with open(p, "wb") as f:
        f.truncate(int(gb * 1e9))  # sparse: only the size matters
    return str(p)


def test_ttft_estimate_without_measurements(app, tmp_path):
    store = app.extensions["pb"].store
    path = _model(tmp_path)
    t = ttft_estimate(store, f"llama-server -m {path} -c 8192 -lm dio -ub 256", disk_gbps=2.0, start_s=10.0)
    assert t["ok"] and t["load"]["source"] == "estimate"
    assert t["load"]["seconds"] == 11.0, "2 GB at 2 GB/s + 10 s start"
    assert t["prefill"]["tps"] is None and all(r["warm_s"] is None for r in t["rows"])
    assert [r["tokens"] for r in t["rows"]] == [512, 2048, 8192], "no prompt longer than the context"
    tips = {x["flag"]: x for x in t["tips"]}
    assert tips["-lm"]["edits"] == {"load-mode": "none"}, "dio skips the page cache"
    assert tips["-ub"]["suggest"] == "512" and tips["--no-warmup"]["edits"] == {"no-warmup": True}
    assert "--cache-reuse" in tips


def test_ttft_uses_measured_loads_and_prompt_speed(app, tmp_path):
    store = app.extensions["pb"].store
    path = _model(tmp_path)
    cmd = f"llama-server -m {path} -c 32768 -lm none -ub 512 --cache-reuse 256 --no-warmup"
    other = f"llama-server -m {path} -c 32768 -lm none -ub 1024 --cache-reuse 256 --no-warmup"
    mid = model_id_for_command(cmd)
    now = time.time()
    s1 = store.open_session({"model_id": mid, "started_at": now - 7200, "last_seen": now, "command": cmd, "load_seconds": 20.0, "load_mode": "none"})
    store.update_session(s1, ended_at=now - 3600, open=0)
    s2 = store.open_session({"model_id": mid, "started_at": now - 3000, "last_seen": now, "command": other, "load_seconds": 30.0, "load_mode": "none"})
    store.update_session(s2, ended_at=now - 1000, open=0)
    store.add_requests([{"source_id": i, "ts": now - 7000 + i, "model": mid, "duration_ms": 1000, "input_tokens": 2000,
                         "output_tokens": 50, "prompt_per_second": 500.0, "tokens_per_second": 30.0} for i in range(3)])
    store.add_requests([{"source_id": 10 + i, "ts": now - 2900 + i, "model": mid, "duration_ms": 1000, "input_tokens": 2000,
                         "output_tokens": 50, "prompt_per_second": 800.0, "tokens_per_second": 30.0} for i in range(3)])
    t = ttft_estimate(store, cmd)
    assert t["load"]["seconds"] == 20.0 and t["load"]["source"] == "measured (this config)"
    assert t["prefill"]["tps"] == 500.0 and t["prefill"]["source"] == "measured (this config)"
    row = next(r for r in t["rows"] if r["tokens"] == 2048)
    assert row["warm_s"] == round(2048 / 500, 2) and row["cold_s"] == round(2048 / 500 + 20, 1)
    assert t["configs"][0]["current"], "the command's own config comes first"
    faster = next(c for c in t["configs"] if not c["current"])
    assert faster["prefill_tps"] == 800.0 and faster["diff"] == {"ubatch-size": "1024"}
    flags = {x["flag"] for x in t["tips"]}
    assert "-lm" not in flags and "--no-warmup" not in flags and "--cache-reuse" not in flags


def test_ttft_bad_command(app):
    assert ttft_estimate(app.extensions["pb"].store, "llama-server 'broken")["ok"] is False
