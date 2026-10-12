"""Build small GGUF files for tests: a real header, with sparse padding standing in for the weights."""
import struct


def kv_str(key, value):
    k, v = key.encode(), value.encode()
    return struct.pack("<Q", len(k)) + k + struct.pack("<I", 8) + struct.pack("<Q", len(v)) + v


def kv_u32(key, value):
    k = key.encode()
    return struct.pack("<Q", len(k)) + k + struct.pack("<I", 4) + struct.pack("<I", value)


def kv_u32_array(key, values):
    k = key.encode()
    return (struct.pack("<Q", len(k)) + k + struct.pack("<I", 9) + struct.pack("<I", 4)
            + struct.pack("<Q", len(values)) + b"".join(struct.pack("<I", v) for v in values))


def kv_str_array(key, values):
    k = key.encode()
    body = b"".join(struct.pack("<Q", len(s)) + s.encode() for s in values)
    return (struct.pack("<Q", len(k)) + k + struct.pack("<I", 9) + struct.pack("<I", 8)
            + struct.pack("<Q", len(values)) + body)


def write_gguf(path, kvs, size=None):
    """Write the header; ``size`` bytes in total (sparse), so os.path.getsize sees a big model."""
    blob = b"GGUF" + struct.pack("<I", 3) + struct.pack("<Q", 0) + struct.pack("<Q", len(kvs)) + b"".join(kvs)
    with open(path, "wb") as f:
        f.write(blob)
        if size and size > len(blob):
            f.truncate(size)


def llama_kvs(layers=32, heads=32, kv_heads=8, emb=4096, train_ctx=32768):
    """The architecture keys of a plain llama-style model, followed by a tokenizer array."""
    return [kv_str("general.architecture", "llama"), kv_u32("llama.block_count", layers),
            kv_u32("llama.attention.head_count", heads), kv_u32("llama.attention.head_count_kv", kv_heads),
            kv_u32("llama.embedding_length", emb), kv_u32("llama.context_length", train_ctx),
            kv_str_array("tokenizer.ggml.tokens", ["a", "b", "c"])]
