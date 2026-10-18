"""The hand-off: the small model declares ESCALATE and the bigger one answers."""
from src.assistant import ESCALATE_RE

SMALL = "qwen3-0.6b-q8_0"
BIG = "qwen3.5-2b-q4_k_m"


def test_the_marker_must_be_its_own_line():
    assert ESCALATE_RE.search("ESCALATE")
    assert ESCALATE_RE.search("  escalate.  ")
    assert ESCALATE_RE.search("Try this.\nESCALATE\n")
    # saying the word is not declaring it
    assert ESCALATE_RE.search("I will not escalate") is None
    assert ESCALATE_RE.search("ESCALATE because it is hard") is None


def test_the_marker_is_stripped_before_the_user_sees_it():
    assert ESCALATE_RE.sub("", "Try this.\nESCALATE\n").strip() == "Try this."


def test_no_escalation_model_means_the_small_one_answers_everything(client, app):
    a = app.extensions["pb"].assistant
    client.put("/api/settings/assistant", json={"mode": "engine", "model": SMALL, "escalate_model": ""})
    client.put("/api/settings/integrations", json={"engine_url": "http://engine"})
    assert a.escalate_model() == ""
    a._escalated = True          # even when a turn says it escalated
    _base, model = a.endpoint()
    assert model == SMALL, "with nothing configured there is nowhere to hand off to"


def test_a_handed_over_turn_is_answered_by_the_bigger_model(client, app):
    a = app.extensions["pb"].assistant
    client.put("/api/settings/assistant", json={"mode": "engine", "model": SMALL, "escalate_model": BIG})
    client.put("/api/settings/integrations", json={"engine_url": "http://engine"})
    assert a.escalate_model() == BIG

    a._escalated = False
    assert a.endpoint()[1] == SMALL, "the small model answers first"

    a._escalated = True
    assert a.endpoint()[1] == BIG, "after the hand-off the bigger model answers"


def test_each_model_keeps_its_own_quant_default(client, app):
    """The assistant's model lives on the GPU; the escalation model may not."""
    cfg = app.extensions["pb"].settings.get("assistant")
    assert cfg["quant"] == "Q8_0"
    assert cfg["escalate_quant"] == "Q4_K_M"


def test_a_local_file_is_picked_by_the_quant_that_was_asked_for(tmp_path):
    from src.routes.assistant import _local_model

    d = tmp_path / "models"
    d.mkdir()
    for name in ("Qwen3-0.6B-Q8_0.gguf", "Qwen3-0.6B-Q4_K_M.gguf"):
        (d / name).write_bytes(b"x" * 10)
    assert _local_model(str(d), r"qwen3-0\.6b", "Q8_0")["quant"] == "Q8_0"
    assert _local_model(str(d), r"qwen3-0\.6b", "Q4_K_M")["quant"] == "Q4_K_M"


def test_the_escalation_model_has_its_own_repo():
    from src.assistant import ESCALATE_REPOS

    assert ESCALATE_REPOS and all("2B" in r or "2b" in r for r in ESCALATE_REPOS), ESCALATE_REPOS
