"""Tips on a profile's GPU layout (advice.py)."""
from src.advice import advise

M = "llama-server -m /m/x.gguf"


def kinds(tips):
    return [t["kind"] for t in tips]


def test_Advice_a_model_that_fits_one_card_spread_over_two():
    assert kinds(advise(f"{M} -c 8192", 20.0, [32.0, 32.0])) == ["copies"]
    assert kinds(advise(f"{M} -c 8192", 40.0, [32.0, 32.0])) == [], "needs both cards"
    assert kinds(advise(f"{M} -sm none", 20.0, [32.0, 32.0])) == [], "already on one card"
    assert kinds(advise(f"HIP_VISIBLE_DEVICES=1 {M}", 20.0, [32.0, 32.0])) == [], "pinned to a card"
    assert kinds(advise(f"{M} -ngl 0", 20.0, [32.0, 32.0])) == [], "on the CPU"
    assert kinds(advise(f"{M}", 20.0, [32.0])) == [], "one card: nothing to split"


def test_Advice_row_or_tensor_split():
    assert "split" in kinds(advise(f"{M} -sm row", 50.0, [32.0, 32.0]))
    assert "split" in kinds(advise(f"{M} --split-mode tensor", 50.0, [32.0, 32.0]))
    assert "split" not in kinds(advise(f"{M} -sm layer", 50.0, [32.0, 32.0]))


def test_Advice_ubatch_from_the_measured_reading_speed():
    t = advise(f"{M} --parallel 2 -ub 2048", 50.0, [32.0, 32.0], prefill_tps=1000)
    tip = next(x for x in t if x["kind"] == "ubatch")
    assert "-ub 192" in tip["title"] and "2.05 s" in tip["why"]
    assert "ubatch" not in kinds(advise(f"{M} --parallel 2 -ub 128", 50.0, [32.0, 32.0], prefill_tps=1000)), "0.13 s: fine"
    assert "ubatch" not in kinds(advise(f"{M} -ub 2048", 50.0, [32.0, 32.0], prefill_tps=1000)), "one chat: nobody waits"
    assert "ubatch" in kinds(advise(f"{M} -np 3 -ub 1024", 50.0, [32.0, 32.0])), "speed not known yet: a general tip"
