"""The Builder's VRAM card: a column past the trained context, for stretching the model with RoPE."""
from src.schedule_rows import ROPE_CTX, estimate_command

from .ggufs import llama_kvs, write_gguf


def test_the_kv_table_offers_one_rope_column_past_the_trained_context(tmp_path):
    path = str(tmp_path / "m-Q4_K_M.gguf")
    write_gguf(path, llama_kvs(train_ctx=262144), size=4 << 30)
    e = estimate_command(f"llama-server -m {path} -c 32768", compare=True)
    assert e["ok"] and e["train_ctx"] == 262144 and e["rope_ctx"] == ROPE_CTX == 1048576
    assert e["compare"]["ctx"][-1] == ROPE_CTX and 524288 not in e["compare"]["ctx"], "the trained steps, then 1M"
    at = {t["type"]: t["kv_gib"] for t in e["compare"]["types"]}
    assert at["f16"][-1] > at["f16"][-2] * 3.9, "four times the trained context costs about four times the cache"


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
