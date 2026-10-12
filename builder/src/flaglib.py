"""Reference list of llama-server flags for the Flags view: what each does, with a few notes from running them.

Flags change between llama.cpp builds. This is a guide, not the truth for your build: ``llama-server --help`` is.
The Flags view also shows how many of your saved profiles use each one and with which values.
"""
from __future__ import annotations

# (canonical name as command.py spells it, category, what it does, note)
# A note is only written where it is useful and safe to say; blank otherwise.
_ROWS: list[tuple[str, str, str, str]] = [
    # --- model
    ("model", "Model", "Path of the GGUF file to load.", "A split model is loaded from its first part (…-00001-of-0000N.gguf)."),
    ("hf-repo", "Model", "Download and load a model from Hugging Face (user/model[:quant]).", "Argos' Models page downloads into your models folder instead, so every server sees the file."),
    ("alias", "Model", "Name the server reports for the model in its API.", ""),
    ("mmproj", "Multimodal", "Vision/audio projector file that goes with the model.", "Without it a multimodal model starts as text-only."),
    ("image-max-tokens", "Multimodal", "Upper limit on tokens used per image.", "Raise for fine detail, lower to save context."),
    # --- context and memory
    ("ctx-size", "Context & memory", "Context window in tokens. The KV cache for all of it is allocated at load.", "With --parallel N each slot gets about ctx/N."),
    ("parallel", "Context & memory", "Number of server slots: how many requests are decoded at once.", "Set it per profile to the chats you really run at once."),
    ("cache-type-k", "Context & memory", "Data type of the K half of the KV cache (f16, q8_0, q4_0 …).", "Argos' builder sets K and V together with one KV cache control."),
    ("cache-type-v", "Context & memory", "Data type of the V half of the KV cache.", "A quantized V cache needs flash attention."),
    ("kv-unified", "Context & memory", "Use one shared KV buffer for all slots instead of one per slot.", ""),
    ("no-kv-offload", "Context & memory", "Keep the KV cache in system RAM instead of VRAM.", "Saves VRAM at a speed cost."),
    ("swa-full", "Context & memory", "Keep the full-size cache for sliding-window-attention models.", "Uses more memory; avoids re-processing the prompt."),
    ("mmap", "Context & memory", "Memory-map the model file instead of reading it.", ""),
    ("no-mmap", "Context & memory", "Read the whole model into memory instead of memory-mapping it.", ""),
    ("mlock", "Context & memory", "Lock the model in RAM so the OS cannot swap it out.", ""),
    ("load-mode", "Context & memory", "How the weights are read from disk (a custom-build option; dio = direct I/O).", "Not in every llama.cpp build. Some ROCm setups hang on mmap; there it is passed as -lm dio."),
    ("fit", "Context & memory", "Shrink unset settings automatically so the model fits in free device memory (on / off).", "Where the build has it, leaving it on stops a small model allocating a huge cache."),
    # --- GPU and offload
    ("n-gpu-layers", "GPU & offload", "How many layers go to the GPU. A large number (or all) offloads everything.", ""),
    ("split-mode", "GPU & offload", "How a model is split over several GPUs: none, layer or row.", "none keeps it on one card; layer spreads whole layers."),
    ("tensor-split", "GPU & offload", "Share of the model per GPU, e.g. 3,1.", "Do not set it on models the scheduler may place on one card."),
    ("main-gpu", "GPU & offload", "The GPU used when the model is not split.", "Pinning with HIP_VISIBLE_DEVICES / CUDA_VISIBLE_DEVICES is more airtight."),
    ("device", "GPU & offload", "Which devices to use, by name.", ""),
    ("n-cpu-moe", "GPU & offload", "Keep the expert weights of the first N mixture-of-experts layers on the CPU.", "Lets a big MoE model fit with less VRAM."),
    ("cpu-moe", "GPU & offload", "Keep all mixture-of-experts weights on the CPU.", ""),
    ("override-tensor", "GPU & offload", "Place tensors matching a pattern on a chosen device (pattern=device).", ""),
    # --- speed
    ("flash-attn", "Speed", "Flash attention (on / off / auto).", "Needed to quantize the V cache; recommended on both ROCm and CUDA."),
    ("batch-size", "Speed", "Logical batch size for prompt processing.", ""),
    ("ubatch-size", "Speed", "Physical batch size: how many tokens go through the GPU at a time.", "Bigger can speed up prompt processing and uses more memory."),
    ("threads", "Speed", "CPU threads used for generation.", ""),
    ("threads-batch", "Speed", "CPU threads used for prompt processing.", ""),
    ("cont-batching", "Speed", "Continuous batching: new requests join batches already running.", "On by default in recent builds."),
    ("no-cont-batching", "Speed", "Turn continuous batching off.", ""),
    ("no-warmup", "Speed", "Skip the warm-up run after loading.", "Faster to become ready; the first request is slower."),
    # --- speculative decoding
    ("model-draft", "Speculative decoding", "A small draft model that proposes tokens for the main model to check.", ""),
    ("spec-type", "Speculative decoding", "Kind of speculative decoding (draft, draft-mtp, ngram-… ).", "draft-mtp is for models with an MTP head, in builds that have it."),
    ("spec-draft-n-max", "Speculative decoding", "Most tokens drafted per step.", ""),
    ("draft-max", "Speculative decoding", "Most tokens drafted per step (older spelling).", ""),
    ("draft-min", "Speculative decoding", "Fewest tokens drafted per step.", ""),
    ("gpu-layers-draft", "Speculative decoding", "GPU layers for the draft model.", ""),
    # --- chat and tools
    ("jinja", "Chat & tools", "Use the model's Jinja chat template. Needed for tool calling.", "Recent builds have it on by default; add it if tools are ignored."),
    ("chat-template", "Chat & tools", "Override the chat template (name or text).", ""),
    ("chat-template-kwargs", "Chat & tools", "Extra variables for the template, as JSON.", "e.g. switch a model's thinking mode on or off."),
    ("reasoning-budget", "Chat & tools", "Cap on thinking tokens (0 turns thinking off, -1 unlimited).", ""),
    # --- sampling
    ("temp", "Sampling", "Temperature: higher is more random.", "Clients usually send their own; this is the server default."),
    ("top-k", "Sampling", "Only the K most likely tokens are considered.", ""),
    ("top-p", "Sampling", "Only the smallest set of tokens whose probability adds to P is considered.", ""),
    ("min-p", "Sampling", "Drop tokens less likely than P times the best one.", ""),
    ("repeat-penalty", "Sampling", "Penalty on repeating recent tokens (1.0 is off).", ""),
    ("seed", "Sampling", "Random seed (-1 picks one).", ""),
    ("n-predict", "Sampling", "Most tokens to generate per request (-1 is no limit).", ""),
    # --- position encoding
    ("rope-scaling", "Context & memory", "RoPE scaling method for longer contexts (none, linear, yarn).", ""),
    ("rope-freq-scale", "Context & memory", "RoPE frequency scaling factor.", ""),
    ("rope-freq-base", "Context & memory", "RoPE base frequency.", ""),
    # --- embeddings
    ("embedding", "Embeddings", "Run as an embeddings server.", "Use with --pooling for the right vector."),
    ("pooling", "Embeddings", "How token vectors become one embedding (none, mean, cls, last, rank).", ""),
    ("reranking", "Embeddings", "Run as a reranker.", ""),
    # --- server
    ("host", "Server", "Address to listen on. 0.0.0.0 is every interface.", ""),
    ("port", "Server", "Port to listen on.", "Argos assigns a free one with ${PORT}."),
    ("no-webui", "Server", "Turn off the built-in web page.", ""),
    ("metrics", "Server", "Expose Prometheus metrics at /metrics.", ""),
    ("verbose", "Server", "More logging.", ""),
]

FLAG_LIBRARY: list[dict] = [
    {"flag": name, "category": cat, "description": text, "notes": note} for name, cat, text, note in _ROWS
]

__all__ = ["FLAG_LIBRARY"]
