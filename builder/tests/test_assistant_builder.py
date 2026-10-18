"""The Builder panel: the assistant sees what is on screen, and can propose a flag edit."""
from src.assistant import _builder_line


def test_the_builder_line_says_what_is_on_screen():
    line = _builder_line({
        "command": "llama-server -m /m/x.gguf -ngl 99 -c 32768",
        "flags": {"model": "/m/x.gguf", "n-gpu-layers": "99", "flash-attn": "on", "HIP_VISIBLE_DEVICES": "0"},
        "env": "HIP_VISIBLE_DEVICES=0",
        "models": ["qwen3-0.6b-q8_0"],
    })
    assert "command: llama-server -m /m/x.gguf" in line
    assert "n-gpu-layers=99" in line and "flash-attn=on" in line
    assert "env: HIP_VISIBLE_DEVICES=0" in line
    assert "comparing against: qwen3-0.6b-q8_0" in line


def test_an_env_var_is_not_offered_as_a_flag():
    line = _builder_line({"flags": {"HIP_VISIBLE_DEVICES": "0", "n-gpu-layers": "99"}})
    assert "n-gpu-layers=99" in line and "HIP_VISIBLE_DEVICES=0" not in line


def test_an_empty_builder_says_so():
    assert _builder_line({}) == "(nothing set yet)"
    assert _builder_line({"command": "   "}) == "(nothing set yet)"


def test_a_flag_edit_is_offered(client, app):
    text, actions = app.extensions["pb"].assistant._actions("Try this.\nACTION set --flash-attn off")
    assert actions == [{"verb": "set", "flag": "--flash-attn", "value": "off", "model": ""}]
    assert "ACTION" not in text and "Try this." in text


def test_an_unset_needs_no_value(client, app):
    _text, actions = app.extensions["pb"].assistant._actions("ACTION unset --cache-type-k")
    assert actions[0]["verb"] == "unset" and actions[0]["flag"] == "--cache-type-k"


def test_a_flag_that_is_not_a_flag_is_never_a_button(client, app):
    """A flag edit is written into a command line, so only a flag's shape is offered."""
    for bad in ["ACTION set $(curl http://x) y", "ACTION set rm -rf /", "ACTION set `whoami` x",
                "ACTION set --x;ls y", "ACTION set ../../etc/passwd x"]:
        assert app.extensions["pb"].assistant._actions(bad)[1] == [], bad


def test_the_digest_carries_the_builder_for_the_turn(client, app):
    a = app.extensions["pb"].assistant
    assert "BUILDER" not in a.digest()
    a._builder = {"command": "llama-server -m /m/x.gguf"}
    try:
        assert "BUILDER" in a.digest() and "llama-server -m /m/x.gguf" in a.digest()
    finally:
        a._builder = None
    assert "BUILDER" not in a.digest()
