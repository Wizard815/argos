import pytest

from src.command import canonical, guess_base_model, parse_command, swap_model_id


def test_parses_env_program_and_aliases():
    cmd = parse_command("CUDA_VISIBLE_DEVICES=0 HF_TOKEN=x ./llama-server -m /m/q.gguf -c 8192 "
                        "-ngl 99 -fa on -ctk q8_0 --port 8080 --no-mmap")
    assert cmd.env == {"CUDA_VISIBLE_DEVICES": "0", "HF_TOKEN": "x"}
    assert cmd.program == "./llama-server"
    s = cmd.summary()
    assert s["model"] == "/m/q.gguf"
    assert s["ctx_size"] == 8192
    assert s["n_gpu_layers"] == "99"
    assert s["flash_attn"] == "on"
    assert s["cache_type_k"] == "q8_0"
    assert s["port"] == 8080
    assert s["mmap"] is False


def test_line_continuations_and_comment_lines():
    cmd = parse_command("llama-server \\\n  # comment line\n  -m a.gguf \\\n  -c 4096")
    assert cmd.argv() == ["llama-server", "-m", "a.gguf", "-c", "4096"]


def test_boolean_flag_does_not_swallow_next_flag():
    # The first version treated every --flag as taking the next token.
    cmd = parse_command("llama-server --jinja --port 8080 --cont-batching -c 2048")
    assert cmd.get("jinja") == ""
    assert cmd.get("port") == "8080"
    assert cmd.get("cont-batching") == ""
    assert cmd.get("ctx-size") == "2048"


def test_flash_attn_value_is_optional():
    assert parse_command("llama-server -fa --port 1").get("flash-attn") == ""
    assert parse_command("llama-server -fa auto --port 1").get("flash-attn") == "auto"
    assert parse_command("llama-server -fa").summary()["flash_attn"] == "on"


def test_negative_numbers_are_values_and_unknown_flags_guess():
    cmd = parse_command("llama-server --n-gpu-layers -1 --foo bar --baz --qux 3")
    assert cmd.get("n-gpu-layers") == "-1"
    assert cmd.get("foo") == "bar"
    assert cmd.get("baz") == ""
    assert cmd.get("qux") == "3"


def test_edits_keep_position_and_spelling():
    cmd = parse_command("llama-server -m a.gguf -ngl 20 --port=9000 -c 4096")
    out = cmd.with_edits({"n-gpu-layers": 99, "--port": 9100, "ctx-size": None, "jinja": True})
    assert out.to_string() == "llama-server -m a.gguf -ngl 99 --port=9100 --jinja"
    assert cmd.to_string() == "llama-server -m a.gguf -ngl 20 --port=9000 -c 4096"  # original untouched


def test_mmap_and_no_mmap_are_exclusive():
    cmd = parse_command("llama-server --no-mmap").with_edits({"mmap": True})
    assert cmd.argv() == ["llama-server", "--mmap"]
    assert cmd.with_edits({"no-mmap": True}).argv() == ["llama-server", "--no-mmap"]


def test_arg_edits_target_repeated_flags():
    cmd = parse_command("llama-server -ot a=CPU -ot b=CPU -ot c=CPU")
    out = cmd.with_edits(arg_edits={"1": "b=CUDA0", "2": None})
    assert out.argv() == ["llama-server", "-ot", "a=CPU", "-ot", "b=CUDA0"]
    with pytest.raises(ValueError):
        cmd.with_edits(arg_edits={"7": "x"})


def test_env_replacement_and_quoting_round_trip():
    cmd = parse_command("llama-server -m '/models/my model.gguf' --alias \"it's\"")
    out = cmd.with_edits(env={"A": "1 2"})
    text = out.to_string()
    assert text.startswith("A='1 2' llama-server")
    again = parse_command(text)
    assert again.get("model") == "/models/my model.gguf"
    assert again.get("alias") == "it's"
    assert again.env == {"A": "1 2"}
    assert parse_command(out.to_string(multiline=True)).argv() == again.argv()


def test_parse_errors():
    with pytest.raises(ValueError):
        parse_command("llama-server -m 'unterminated")
    with pytest.raises(ValueError):
        parse_command("FOO=1")
    with pytest.raises(ValueError):
        parse_command("")


def test_canonical_and_guess_base_model():
    assert canonical("-ngl") == "n-gpu-layers"
    assert canonical("--gpu-layers") == "n-gpu-layers"
    assert canonical("--something-new") == "something-new"
    assert guess_base_model("/models/Qwen3-8B-Q4_K_M.gguf") == "Qwen3-8B-Q4_K_M"
    assert guess_base_model("/m/GLM-4.5-Air-Q4_K_M-00001-of-00002.gguf") == "GLM-4.5-Air-Q4_K_M"
    assert guess_base_model("unsloth/Qwen3-8B-GGUF:Q4_K_M") == "Qwen3-8B-GGUF:Q4_K_M"
    assert guess_base_model("") == ""




def test_model_id_is_the_file_name_on_any_platform():
    assert swap_model_id("/app/models/Qwen3-8B-Q4_K_M.gguf") == "qwen3-8b-q4_k_m"
    assert swap_model_id(r"C:\Users\me\models\gemma\Gemma-4-12B-UD-Q8_K_XL.gguf") == "gemma-4-12b-ud-q8_k_xl"
    assert swap_model_id("") == "model"
