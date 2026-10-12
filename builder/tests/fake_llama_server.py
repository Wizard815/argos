"""Stand-in for llama-server used by the tests and for UI demos without a GPU.

Accepts llama-server style flags (only --port/--host/-c/-ngl matter), prints
load lines in llama.cpp's format, serves /health and /completion, and prints
a per-request timing block exactly like llama-server does.
"""
import argparse
import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("-c", "--ctx-size", type=int, default=4096)
    p.add_argument("-ngl", "--n-gpu-layers", default="99")
    p.add_argument("--load-delay", type=float, default=0.3)
    args, _unknown = p.parse_known_args()

    ngl = 33 if args.n_gpu_layers in ("99", "999", "all") else min(int(args.n_gpu_layers), 33)
    speed = 40.0 + ngl * 2  # more layers on GPU -> faster, so tuning shows up
    log = sys.stdout
    print("load_tensors: loading model tensors, this can take a while... (mmap = true)", file=log)
    print(f"load_tensors: offloaded {ngl}/33 layers to GPU", file=log)
    print("load_tensors:        CUDA0 model buffer size =  4403.49 MiB", file=log)
    print("load_tensors:   CPU_Mapped model buffer size =   281.81 MiB", file=log)
    print(f"llama_context: n_ctx         = {args.ctx_size}", file=log)
    print("llama_kv_cache:      CUDA0 KV buffer size =   576.00 MiB", file=log)
    print("llama_context:      CUDA0 compute buffer size =   296.00 MiB", file=log)
    log.flush()
    time.sleep(args.load_delay)
    print(f"main: server is listening on http://{args.host}:{args.port} - starting the main loop", file=log)
    log.flush()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):  # keep the captured log readable
            pass

        def _json(self, code, obj):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/health":
                self._json(200, {"status": "ok"})
            else:
                self._json(404, {"error": "not found"})

        def _chat(self, req):
            """/v1/chat/completions: echoes the last user message and what context it got."""
            msgs = req.get("messages") or []
            system = msgs[0]["content"] if msgs and msgs[0]["role"] == "system" else ""
            last = next((m["content"] for m in reversed(msgs) if m["role"] == "user"), "")
            if "memory notes" in system:
                text = f"SUMMARY of {last.count('User:')} user turns."
            else:
                text = (f"You said: {last}. history={len(msgs) - 1}"
                        + (" live=yes" if "LIVE DATA" in system else "")
                        + (" summary=yes" if "summary)" in system else ""))
                if "think" in last:
                    text = "<think>internal reasoning</think>" + text
            print(f"chat: {len(msgs)} messages", file=log)
            log.flush()
            if not req.get("stream"):
                self._json(200, {"choices": [{"message": {"role": "assistant", "content": text}}]})
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for i in range(0, len(text), 7):
                chunk = {"choices": [{"delta": {"content": text[i:i + 7]}}]}
                self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
            self.wfile.write(b"data: [DONE]\n\n")

        def do_POST(self):
            req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            if self.path == "/v1/chat/completions":
                self._chat(req)
                return
            if self.path != "/completion":
                self._json(404, {"error": "not found"})
                return
            n_prompt = max(1, int(len(req.get("prompt", "").split()) * 1.3))
            n_pred = int(req.get("n_predict", 16))
            p_ms, e_ms = n_prompt / (speed * 20) * 1000, n_pred / speed * 1000
            print("slot print_timing: id  0 | task 0 | ", file=log)
            print(f"prompt eval time = {p_ms:10.2f} ms / {n_prompt:5d} tokens ({p_ms / n_prompt:8.2f} ms per token, "
                  f"{n_prompt / p_ms * 1000:8.2f} tokens per second)", file=log)
            print(f"       eval time = {e_ms:10.2f} ms / {n_pred:5d} tokens ({e_ms / n_pred:8.2f} ms per token, "
                  f"{n_pred / e_ms * 1000:8.2f} tokens per second)", file=log)
            print(f"      total time = {p_ms + e_ms:10.2f} ms / {n_prompt + n_pred:5d} tokens", file=log)
            log.flush()
            self._json(200, {"content": "ok " * n_pred, "tokens_evaluated": n_prompt,
                             "tokens_predicted": n_pred, "timings": {
                                 "prompt_n": n_prompt, "prompt_ms": p_ms,
                                 "prompt_per_second": n_prompt / p_ms * 1000,
                                 "predicted_n": n_pred, "predicted_ms": e_ms,
                                 "predicted_per_second": n_pred / e_ms * 1000}})

    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
