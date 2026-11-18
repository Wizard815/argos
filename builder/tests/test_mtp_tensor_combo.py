"""An MTP draft with --split-mode tensor gets its companion flag at launch: the draft whole on one card."""
from src.always import apply_always, combo_fixes
from src.command import parse_command

GEMMA = ("/app/bin/remote-llama rocm_srv3 --model /m/Gemma4-26B-Q4_K_M.gguf -ngl 99 -c 131072 --flash-attn on "
         "--cache-type-k q8_0 --cache-type-v q8_0 -md /m/mtp-gemma-4-26B-A4B-it.gguf --spec-type draft-mtp "
         "--spec-draft-n-max 3 --split-mode tensor")


def run(command: str) -> tuple[str, list[str]]:
    return apply_always(command, {})


def test_TensorMtp_the_gemma_preset_gets_the_draft_on_one_card_and_no_draft_cache_type():
    full, added = run(GEMMA)
    cmd = parse_command(full)
    assert cmd.get("device-draft") == "ROCm0"
    assert cmd.get("cache-type-k-draft") is None, "Gemma 4's draft shares the main cache: a different type fails to load"
    assert added == ["--device-draft ROCm0"]


def test_TensorMtp_built_in_mtp_layers_count_too():
    full, added = run("llama-server -m /m/Qwen3.6-35B-A3B-MTP.gguf --spec-type draft-mtp -sm tensor -ctk q4_0 -ctv q4_0")
    assert "--device-draft ROCm0" in added and parse_command(full).get("device-draft") == "ROCm0"


def test_TensorMtp_what_you_set_yourself_wins_and_is_recognised_in_either_spelling():
    full, added = run(GEMMA + " -devd ROCm1")
    assert added == [] and parse_command(full).get("device-draft") == "ROCm1"


def test_TensorMtp_layer_split_or_no_mtp_is_left_alone():
    assert run(GEMMA.replace("--split-mode tensor", "--split-mode layer"))[1] == []
    assert run("llama-server -m /m/a.gguf -sm tensor -md /m/small-draft.gguf --spec-type draft")[1] == []
    assert run("llama-server -m /m/a.gguf -c 8192")[1] == []


def test_TensorMtp_the_draft_goes_on_the_first_listed_device_or_card_0_of_the_runtime():
    assert combo_fixes(parse_command(GEMMA + " --device ROCm1,ROCm0"))[0] == "--device-draft ROCm1"
    cuda = "/app/bin/remote-llama cuda --model /m/x.gguf --spec-type draft-mtp --split-mode tensor"
    assert combo_fixes(parse_command(cuda))[0] == "--device-draft CUDA0"


def test_TensorMtp_always_set_flags_and_the_fix_both_apply():
    full, added = apply_always(GEMMA, {"flags": "--metrics", "env": ""})
    assert "--metrics" in full and "--device-draft ROCm0" in added
