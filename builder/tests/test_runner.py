import time

import pytest

from src.runner import LaunchError, Runner, bench_prompt, parse_nvidia_smi

from .conftest import fake_command


def wait_ready(runner, run, timeout=15.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if runner.health(run):
            return
        time.sleep(0.1)
    raise AssertionError("fake server never became ready:\n" + (runner.read_log(run.launch_id) or ""))


def test_parse_nvidia_smi():
    assert parse_nvidia_smi("45, 8123, 61\n90, 2048, 70\n") == {"gpu_util": 90.0, "mem_used_gb": 9.93, "temp_c": 70.0}
    assert parse_nvidia_smi("[N/A], 100, 40\n") == {}
    assert parse_nvidia_smi("") == {}


def test_bench_prompt_length():
    assert len(bench_prompt(400).split()) == 400 + 3  # + "Continue the ledger:"


def test_launch_missing_program(tmp_path):
    runner = Runner(str(tmp_path))
    with pytest.raises(LaunchError, match="could not start"):
        runner.launch("definitely-not-a-real-binary-xyz --port 1")
    with pytest.raises(LaunchError, match="could not parse"):
        runner.launch("llama-server -m 'oops")


def test_missing_program_says_which_setting_points_it_somewhere_else(tmp_path):
    """'No such file or directory' on its own reads like a bad model path.

    What is missing is the program, and when llama.cpp lives in another
    container the fix is a setting, so the message has to name it and say what
    to put there.
    """
    runner = Runner(str(tmp_path))
    with pytest.raises(LaunchError) as exc:
        runner.launch("definitely-not-a-real-binary-xyz --port 1")
    message = str(exc.value)
    assert "not found in this container" in message
    assert "Launch program" in message


def test_launch_bench_stop(tmp_path):
    runner = Runner(str(tmp_path))
    run = runner.launch(fake_command("-ngl", "99", "-c", "2048"), base_model="fake")
    try:
        assert run.port and "${PORT}" not in run.command
        wait_ready(runner, run)
        t = runner.bench(run, prompt_words=200, n_predict=32)
        assert t["output_tokens"] == 32 and t["tokens_per_second"] > 0 and t["prompt_per_second"] > 0
        log = runner.read_log(run.launch_id)
        assert log.startswith("$ ") and "eval time" in log
        assert len(runner.read_log(run.launch_id, tail_bytes=50)) == 50
    finally:
        assert runner.stop(run.launch_id)
    assert not run.running and run.stopped
    assert runner.read_log("run-missing") is None
    with pytest.raises(LaunchError, match="not reachable"):
        runner.bench(run)


def test_stop_all_kills_everything(tmp_path):
    runner = Runner(str(tmp_path))
    runs = [runner.launch(fake_command()) for _ in range(2)]
    runner.stop_all()
    assert all(not r.running for r in runs)
