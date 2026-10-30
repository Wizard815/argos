from src.log_analyzer import parse_log_text

SERVER_LOG = """\
load_tensors: offloaded 29/37 layers to GPU
load_tensors:        CUDA0 model buffer size =  4403.49 MiB
load_tensors:   CPU_Mapped model buffer size =   281.81 MiB
llama_context: n_ctx         = 8192
llama_context:  CUDA_Host  output buffer size =     0.58 MiB
llama_kv_cache:      CUDA0 KV buffer size =   576.00 MiB
llama_context:      CUDA0 compute buffer size =   296.00 MiB
llama_context:  CUDA_Host compute buffer size =    24.01 MiB
main: server is listening on http://127.0.0.1:8080 - starting the main loop
slot print_timing: id  0 | task 0 |
prompt eval time =      58.33 ms /    13 tokens (    4.49 ms per token,   222.87 tokens per second)
       eval time =    1007.31 ms /    64 tokens (   15.74 ms per token,    63.54 tokens per second)
      total time =    1065.64 ms /    77 tokens
draft acceptance rate = 0.68208 (  118 accepted /   173 generated)
slot print_timing: id  0 | task 1 |
prompt eval time =     812.00 ms /  2048 tokens (    0.40 ms per token,  2522.17 tokens per second)
       eval time =    2000.00 ms /   128 tokens (   15.63 ms per token,    64.00 tokens per second)
"""


def test_llama_server_log_last_request_wins():
    r = parse_log_text(SERVER_LOG)
    assert r.samples == 2
    assert (r.input_tokens, r.prompt_per_second, r.prompt_ms) == (2048, 2522.17, 812.0)
    assert (r.output_tokens, r.tokens_per_second, r.eval_ms) == (128, 64.0, 2000.0)
    assert r.duration_ms == 2812
    assert r.draft_acc == 0.68208 and r.draft_tokens == 173
    assert r.n_ctx == 8192
    assert (r.layers_offloaded, r.layers_total) == (29, 37)
    assert r.gpu_mem_mb == 4403.49 + 576.0 + 296.0  # CPU and *_Host buffers excluded
    assert r.ready is True
    assert r.error == ""


def test_llama_cli_timing_formats():
    old = """\
llama_print_timings:        load time =     715.56 ms
llama_print_timings:      sample time =      10.03 ms /    64 runs   (    0.16 ms per token,  6380.86 tokens per second)
llama_print_timings: prompt eval time =     139.26 ms /    13 tokens (   10.71 ms per token,    93.35 tokens per second)
llama_print_timings:        eval time =    1373.35 ms /    63 runs   (   21.80 ms per token,    45.87 tokens per second)
"""
    r = parse_log_text(old)
    assert (r.prompt_per_second, r.tokens_per_second, r.output_tokens) == (93.35, 45.87, 63)
    assert r.load_ms == 715.56

    new = """\
llama_perf_sampler_print:    sampling time =       5.10 ms /    40 runs   (    0.13 ms per token,  7843.14 tokens per second)
llama_perf_context_print: prompt eval time =      48.20 ms /     8 tokens (    6.03 ms per token,   165.98 tokens per second)
llama_perf_context_print:        eval time =     512.00 ms /    31 runs   (   16.52 ms per token,    60.55 tokens per second)
"""
    r = parse_log_text(new)
    assert (r.input_tokens, r.prompt_per_second, r.tokens_per_second) == (8, 165.98, 60.55)


def test_llama_bench_table():
    table = """\
| model                          |       size |     params | backend    | ngl |            test |                  t/s |
| ------------------------------ | ---------: | ---------: | ---------- | --: | --------------: | -------------------: |
| qwen3 8B Q4_K - Medium         |   4.68 GiB |     8.19 B | CUDA       |  99 |           pp512 |      5012.33 ± 21.10 |
| qwen3 8B Q4_K - Medium         |   4.68 GiB |     8.19 B | CUDA       |  99 |           tg128 |        142.07 ± 0.31 |
| qwen3 8B Q4_K - Medium         |   4.68 GiB |     8.19 B | CUDA       |  99 |    tg128 @ d4096 |        131.50 ± 0.20 |
"""
    r = parse_log_text(table)
    assert (r.input_tokens, r.prompt_per_second) == (512, 5012.33)
    assert (r.output_tokens, r.tokens_per_second) == (128, 131.5)


def test_errors_and_bad_numbers():
    r = parse_log_text("llama_model_load: error loading model: failed to open /nope.gguf\n"
                       "       eval time =       0.00 ms /     0 tokens (    nan ms per token,      inf tokens per second)\n")
    assert r.error.startswith("llama_model_load: error loading model")
    assert r.tokens_per_second == 0.0
    assert parse_log_text("").samples == 0


def test_a_long_prompt_keeps_its_fastest_and_slowest_stretch():
    log = "\n".join([
        "slot print_timing: id  0 | task 3 | prompt processing, n_tokens =    512, progress = 0.50, t =   0.20 s / 2560.00 tokens per second",
        "slot print_timing: id  0 | task 3 | prompt processing, n_tokens =   1024, progress = 1.00, t =   0.60 s / 1706.67 tokens per second",
        "slot print_timing: id  0 | task 4 | prompt processing, n_tokens =   4096, progress = 0.25, t =   2.00 s / 2048.00 tokens per second",
        "slot print_timing: id  0 | task 4 | prompt processing, n_tokens =   8192, progress = 0.50, t =   5.00 s / 1638.40 tokens per second",
        "slot print_timing: id  0 | task 4 | prompt processing, n_tokens =  16384, progress = 1.00, t =  13.00 s / 1260.31 tokens per second",
        "prompt eval time =   13000.00 ms / 16384 tokens (    0.79 ms per token,  1260.31 tokens per second)",
    ])
    r = parse_log_text(log)
    assert (r.prefill_high_tps, r.prefill_low_tps) == (2048.0, 1024.0), "the last prompt's, not the one before"
    assert r.prompt_per_second == 1260.31
    assert parse_log_text("prompt eval time = 50 ms / 13 tokens ( 3.8 ms per token, 260 tokens per second)").prefill_high_tps is None
