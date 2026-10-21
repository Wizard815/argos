---
title: Writing the cmd for a model
summary: Minimal model config using cmd and ${PORT}, plus proxy, Docker, and llama-server's --jinja flag.
category: guides
tags: [cmd, port, proxy, model-id, check-endpoint, tools, tool-calling, jinja, llama-server, minimal, configuration, getting-started, docker, container]
config_keys: [models.*.cmd, models.*.proxy, models.*.checkEndpoint, models.*.env, models.*.cwd, startPort]
updated: 2026-10-21
---

# Writing the `cmd` for a model

`cmd` is the only required setting on a model. It is a plain command line —
whatever you would type in a shell to start the server yourself.

Recent `llama-server` builds enable `--jinja` by default. If a model
describes a tool call in prose instead of making one, your build predates
that default — add `--jinja` to `cmd` explicitly. Without it, llama.cpp
silently ignores the request's tools array. The separate `capabilities.tools`
setting only advertises support to clients; it does not enable tool calling
in llama-server.

## Smallest working configuration

A minimal `llama-server` config must pass its assigned `${PORT}` to the
server. Omitting `--port ${PORT}` makes llama-server listen somewhere other
than Argos's default proxy target.

```yaml
models:
  model1:
    cmd: llama-server --port ${PORT} --model /path/to/model.gguf
```

Keep `${PORT}` in a minimal `llama-server` command. It is the host port that
Argos assigns and the default `proxy` uses.

## Multi-line and comments

Use a YAML block scalar (`|`) to break it up. Lines starting with `#` are
stripped before the command runs, so you can annotate flags:

```yaml
cmd: |
  llama-server --port ${PORT}
  --model /models/qwen3-8b.gguf
  --ctx-size 16384
  # only needed on builds older than --jinja's default-on change
  --jinja
  --cache-type-k q8_0
```

## The three built-in macros

| macro | expands to | available in |
| --- | --- | --- |
| `${PORT}` | a free port assigned to this model, starting at `startPort` (default 5800) | `cmd`, `proxy`, `metadata` |
| `${MODEL_ID}` | the model's key from the `models` map | anywhere in the model, and in global macros |
| `${PID}` | the upstream process id | `cmdStop` only |

These names are reserved — you cannot define a macro called `PORT`, `PID` or
`MODEL_ID`.

## `proxy`: when you need it

`proxy` tells Argos where to send requests. It defaults to
`http://localhost:${PORT}`.

- **Used `${PORT}` in `cmd`?** Omit `proxy`. The default is correct.
- **Hardcoded a port in `cmd`?** You *must* set `proxy` to match, or Argos
  proxies to the wrong place and every request times out.
- **Running in a container?** Set it explicitly —
  `proxy: "http://127.0.0.1:${PORT}"` — because the port inside the container
  differs from the one outside. In `-p ${PORT}:8080`, `${PORT}` is the host
  port and `8080` is the container port.

This mismatch is the single most common configuration error. If a model starts
successfully but requests hang or return connection errors, check it first.

## `checkEndpoint`: knowing when it's ready

Argos polls `checkEndpoint` (default `/health`) until it returns HTTP 200,
and holds requests until then. Servers that don't expose `/health` need it
changed:

```yaml
checkEndpoint: /v1/models
```

Set `checkEndpoint: "none"` to skip health checking entirely — Argos then
forwards the first request immediately, which usually means it fails while the
server is still loading weights. Only use it for servers with no usable
readiness endpoint.

## Environment variables

`env` injects variables into the command's environment. Each entry is one
`NAME=value` string:

```yaml
env:
  - "CUDA_VISIBLE_DEVICES=0,1"
  - "HSA_OVERRIDE_GFX_VERSION=11.0.0"
```

This is the right place for per-model GPU pinning. Note that `env` values are
part of your config file — see `guides/api-integration/api-keys-and-auth` for keeping secrets out
of it.

## Model files

Argos has no setting that downloads a missing GGUF from Hugging Face or
another model registry. The file must already exist, or the configured `cmd`
must fetch it before starting the inference server.

## Any OpenAI-compatible server

Nothing here is llama.cpp-specific. vLLM, SGLang, TabbyAPI, Ollama's OpenAI
endpoint and anything else that speaks the API work the same way — start it,
tell Argos the port, point `checkEndpoint` at something that returns 200.

If the upstream expects a different model name than your Argos ID, use
`useModelName` to rewrite it on the way out.

## Servers installed from source: `cwd`

Python servers such as Kokoro or ComfyUI find their files relative to their
own folder, so they must start in it. `cwd` sets the folder the process
starts in; without it the process inherits the engine's own.

```yaml
models:
  kokoro:
    cmd: /data/runners/kokoro/.venv/bin/python -m uvicorn api.src.main:app --host 127.0.0.1 --port ${PORT}
    cwd: /data/runners/kokoro
    checkEndpoint: /health
    aliases: [tts-1]
    vram: 0.1
```

The server must listen on `127.0.0.1` and the port Argos gives it. Set
`vram` for anything that is not a `.gguf`: the size estimate only reads
model files, and a model with no size runs alone.

You rarely write these by hand: **Servers -> Local** installs whisper.cpp,
Kokoro or a server cloned from git into `/data/runners`, and writes their
models (with `cwd` filled in) to `runners.generated.yaml` beside the
schedule's file. A wrong `cwd` shows as the server failing to find its own
model or config files in the model's log.

## Related

- `reference/config/models` — every model setting, annotated
- `guides/configuration/macros` — cutting repetition out of long commands
- `guides/model-runtime/troubleshooting-model-wont-load` — when it doesn't start
