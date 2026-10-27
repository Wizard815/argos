"""The Builder's VRAM card: columns past the trained context (384K, 512K, 768K, 1M), for stretching the model with RoPE."""
from src.schedule_rows import ROPE_CTX, ROPE_STEPS, estimate_command

from .ggufs import llama_kvs, write_gguf


def test_the_kv_table_offers_rope_columns_past_the_trained_context(tmp_path):
    path = str(tmp_path / "m-Q4_K_M.gguf")
    write_gguf(path, llama_kvs(train_ctx=262144), size=4 << 30)
    e = estimate_command(f"llama-server -m {path} -c 32768", compare=True)
    assert e["ok"] and e["train_ctx"] == 262144 and e["rope_ctx"] == ROPE_CTX == 1048576
    ctxs = e["compare"]["ctx"]
    assert ctxs[ctxs.index(262144):] == [262144, 393216, 524288, 786432, ROPE_CTX], "the trained steps, then 384K, 512K, 768K, 1M"
    at = {t["type"]: t["kv_gib"] for t in e["compare"]["types"]}
    trained = at["f16"][ctxs.index(262144)]
    assert at["f16"][-1] > trained * 3.9, "four times the trained context costs about four times the cache"
    assert trained < at["f16"][ctxs.index(393216)] < at["f16"][ctxs.index(524288)] < at["f16"][ctxs.index(786432)] < at["f16"][-1]


def test_a_shorter_trained_model_gets_every_rope_step_past_it(tmp_path):
    path = str(tmp_path / "short-Q4_K_M.gguf")
    write_gguf(path, llama_kvs(train_ctx=131072), size=4 << 30)
    e = estimate_command(f"llama-server -m {path} -c 32768", compare=True)
    assert [c for c in e["compare"]["ctx"] if c > 131072] == list(ROPE_STEPS)


def test_a_model_trained_on_1m_already_gets_no_rope_column(tmp_path):
    path = str(tmp_path / "long-Q4_K_M.gguf")
    write_gguf(path, llama_kvs(train_ctx=ROPE_CTX), size=4 << 30)
    e = estimate_command(f"llama-server -m {path} -c 32768", compare=True)
    assert "rope_ctx" not in e


def test_a_stretched_command_is_estimated_at_its_stretched_context(tmp_path):
    path = str(tmp_path / "m-Q4_K_M.gguf")
    write_gguf(path, llama_kvs(train_ctx=262144), size=4 << 30)
    e = estimate_command(f"llama-server -m {path} -c {ROPE_CTX} --rope-scaling yarn --rope-scale 4 --yarn-orig-ctx 262144", compare=True)
    assert e["ctx"] == ROPE_CTX and e["compare"]["ctx"].count(ROPE_CTX) == 1
