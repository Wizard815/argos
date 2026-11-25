"""Draft model on the CPU: --spec-draft-device (-devd / --device-draft) is one setting, and none keeps the draft out of VRAM."""
import pytest

from src.command import canonical, parse_command
from src.flaglib import FLAG_LIBRARY
from src.schedule_rows import estimate_command

from .ggufs import llama_kvs, write_gguf


@pytest.mark.parametrize("flag", ["--spec-draft-device", "-devd", "--device-draft"])
def test_every_spelling_of_the_draft_device_is_one_setting(flag):
    assert canonical(flag) == "spec-draft-device"
    cmd = parse_command(f"llama-server -m a.gguf -md d.gguf {flag} none")
    s = cmd.summary()
    assert s["spec_draft_device"] == "none" and s["draft_on_cpu"] is True
    # an edit keeps the spelling the command already has
    assert cmd.with_edits({"spec-draft-device": "ROCm1"}).to_string() == f"llama-server -m a.gguf -md d.gguf {flag} ROCm1"


def test_newer_draft_spellings_are_the_same_settings():
    cmd = parse_command("llama-server -m a.gguf --spec-draft-model d.gguf --spec-draft-ngl 0")
    assert cmd.get("model-draft") == "d.gguf" and cmd.get("gpu-layers-draft") == "0"
    assert cmd.summary()["draft_on_cpu"] is True


def test_a_new_draft_device_is_written_in_a_spelling_older_builds_know():
    out = parse_command("llama-server -m a.gguf -md d.gguf").with_edits({"spec-draft-device": "none"})
    assert out.to_string() == "llama-server -m a.gguf -md d.gguf --device-draft none"
    assert out.with_edits({"spec-draft-device": None}).to_string() == "llama-server -m a.gguf -md d.gguf"


@pytest.mark.parametrize("cmd", [
    "llama-server -m a.gguf -md d.gguf",
    "llama-server -m a.gguf -md d.gguf -devd ROCm0",
    "llama-server -m a.gguf -devd none",  # no draft model: nothing to keep on the CPU
])
def test_a_draft_on_a_gpu_is_not_on_the_cpu(cmd):
    assert parse_command(cmd).summary()["draft_on_cpu"] is False


def test_the_flag_library_explains_it():
    assert any(r["flag"] == "spec-draft-device" and "none" in r["description"] for r in FLAG_LIBRARY)


def test_a_draft_on_the_cpu_adds_no_vram(tmp_path):
    model, draft = str(tmp_path / "m-Q4_K_M.gguf"), str(tmp_path / "d-Q8_0.gguf")
    write_gguf(model, llama_kvs(train_ctx=32768), size=4 << 30)
    write_gguf(draft, llama_kvs(train_ctx=32768), size=1 << 30)
    gpu = estimate_command(f"llama-server -m {model} -c 8192 -md {draft}")
    cpu = estimate_command(f"llama-server -m {model} -c 8192 -md {draft} --spec-draft-device none")
    assert gpu["ok"] and cpu["ok"]
    assert gpu["extra_gib"] == pytest.approx(1.0, abs=0.01) and cpu["extra_gib"] == 0
    assert cpu["gib"] < gpu["gib"]
