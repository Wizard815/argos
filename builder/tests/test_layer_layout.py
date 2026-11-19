"""The Builder's model facts: the context a model was trained on and which of its layers keep the KV cache."""
from src.schedule_rows import estimate_command, layer_layout

from .ggufs import kv_str, kv_str_array, kv_u32, kv_u32_array, llama_kvs, write_gguf


def _kvs(arch, layers, kv_heads, train_ctx, extra=()):
    return [kv_str("general.architecture", arch), kv_u32(f"{arch}.block_count", layers),
            kv_u32(f"{arch}.attention.head_count", 32),
            kv_u32_array(f"{arch}.attention.head_count_kv", kv_heads) if isinstance(kv_heads, list) else kv_u32(f"{arch}.attention.head_count_kv", kv_heads),
            kv_u32(f"{arch}.attention.key_length", 128), kv_u32(f"{arch}.attention.value_length", 128),
            kv_u32(f"{arch}.embedding_length", 2048), kv_u32(f"{arch}.context_length", train_ctx), *extra,
            kv_str_array("tokenizer.ggml.tokens", ["a", "b"])]


def test_a_full_attention_model_keeps_every_token_in_every_layer(tmp_path):
    """Qwen3-Omni / Qwen3-30B-A3B: 48 layers, 4 KV heads of 128, trained for 64K; 256K at q5_0 is 8.25 GiB."""
    path = str(tmp_path / "omi-Q8_0.gguf")
    write_gguf(path, _kvs("qwen3vlmoe", 48, 4, 65536), size=8 << 30)
    e = estimate_command(f"llama-server -m {path} -c 262144 -ctk q5_0 -ctv q5_0 --parallel 1")
    L = e["layout"]
    assert L == {"arch": "qwen3vlmoe", "layers": 48, "full_layers": 48, "swa_layers": 0, "swa_window": 0, "no_cache_layers": 0,
                 "kv_heads": 4, "train_ctx": 65536, "kv_per_token_kib": 33.0}
    assert abs(e["kv_gib"] - L["kv_per_token_kib"] * 1024 * 262144 / (1 << 30)) < 0.01, "the per-token figure explains the whole cache"


def test_a_hybrid_model_only_grows_the_cache_in_its_full_layers(tmp_path):
    """Qwen3.5/3.6-style: a 0 in the per-layer KV-head list is a linear layer with no KV cache."""
    path = str(tmp_path / "hybrid-Q4_K_M.gguf")
    heads = [0, 0, 0, 2] * 10
    write_gguf(path, _kvs("qwen35moe", 40, heads, 262144), size=8 << 30)
    L = estimate_command(f"llama-server -m {path} -c 262144 -ctk q8_0 -ctv q8_0")["layout"]
    assert (L["layers"], L["full_layers"], L["no_cache_layers"], L["swa_layers"]) == (40, 10, 30, 0)
    assert L["train_ctx"] == 262144


def test_a_sliding_window_model_names_its_window(tmp_path):
    """Gemma-style: one full layer in six, the rest keep only the last 1024 tokens."""
    path = str(tmp_path / "gemma-Q4_K_M.gguf")
    write_gguf(path, _kvs("gemma3", 36, 8, 131072, (kv_u32("gemma3.attention.sliding_window", 1024),)), size=4 << 30)
    L = estimate_command(f"llama-server -m {path} -c 32768")["layout"]
    assert (L["layers"], L["full_layers"], L["swa_layers"], L["swa_window"]) == (36, 6, 30, 1024)


def test_the_per_token_cost_follows_the_cache_type_and_no_header_means_no_layout(tmp_path):
    path = str(tmp_path / "m-Q4_K_M.gguf")
    write_gguf(path, llama_kvs(layers=32, kv_heads=8, emb=4096, train_ctx=32768), size=4 << 30)
    f16 = estimate_command(f"llama-server -m {path} -c 8192")["layout"]["kv_per_token_kib"]
    q4 = estimate_command(f"llama-server -m {path} -c 8192 -ctk q4_0 -ctv q4_0")["layout"]["kv_per_token_kib"]
    assert f16 == 128.0 and abs(q4 - f16 * 18 / 64) < 0.1
    assert layer_layout(None) is None
