---
title: Odysseus integration
summary: Let Odysseus own a model's launch command and switch to it with a profile, without changing llama-swap's defaults.
category: guides
tags: [odysseus, profiles, integration, launch, commands, cookbook]
config_keys: [odysseus, odysseus.enabled, odysseus.stateFile, odysseus.baseURL, odysseus.token, odysseus.tokenEnv, odysseus.tokenFile, odysseus.refreshSeconds, odysseus.profilePrefix, odysseus.statuses, odysseus.outputFile, odysseus.ensureDio, odysseus.timeoutSeconds]
updated: 2026-09-26
---

# Odysseus integration

Odysseus' Cookbook assembles a complete `llama-server ...` command in the browser
and runs it in tmux, keeping the command it used in `cookbook_state.json`
(`task.payload._cmd`). This integration lets that command drive llama-swap, so
Odysseus stays the author of a model's flags while llama-swap stays the process
supervisor.

Turn it on with `odysseus.enabled`. llama-swap polls Odysseus, and for each model
Odysseus has a command for it generates:

- one **variant model**, id `<base-id>--<profilePrefix>` (and `-2`, `-3` for older
  distinct commands), carrying that command and its environment. The variant
  inherits the base model's display name, so a switch reads as the same model
  with different flags instead of a second model;
- one **profile** whose `pins` rewrite the base model ID to that variant.

The base models are never touched. **With no profile active — the state at
startup and after every config reload — nothing changes**, so the Odysseus
commands are strictly opt-in. Pick one in the header of the web UI, or:

```bash
curl -X PUT http://localhost:8080/api/profiles/active -d '{"name":"ody"}'
curl -X PUT http://localhost:8080/api/profiles/active -d '{"name":null}'   # back to default
```

## Configuration

```yaml
odysseus:
  enabled: true
  baseURL: "http://192.168.20.5:7005"        # or http://odysseus:7000 on a shared network
  token: "${env.ODYSSEUS_INTERNAL_TOKEN}"    # see "Authentication" below
  refreshSeconds: 300
  profilePrefix: "ody"
  outputFile: "/app/config.d/odysseus.generated.yaml"
  ensureDio: true
```

`outputFile` must live in the `-config-dir` so llama-swap's merge and the
`-watch-config` watcher see it. Without `-watch-config` the file is still written,
but nothing picks it up until a restart or another reload trigger.

## What it reads: presets first, then live tasks

`cookbook_state.json` holds two things worth having, and they are not equally
durable.

**`presets`** are the Cookbook's *Save* button (at most five per model). Each
carries the label you gave it and the full launch command:

```json
{"name": "Qwen3.8-27B", "model": "unsloth/Qwen3.8-27B-GGUF",
 "label": "fast", "cmd": "llama-server --model /app/models/... --port 8000 ..."}
```

These become profiles named `<prefix>-<label>`, so activating `ody-fast` applies
the config you labelled `fast` to every model that has one. This is what makes
the integration a per-model profile switch rather than a snapshot of whatever
happened to be running.

**`tasks`** are the currently-tracked launches, and they are the fallback: a task
only carries a command while it exists. Stop the model in the Cookbook and the
entry moves to `removedTasks`, which records only an id and a timestamp — the
command is gone. Those become `<prefix>` (newest first) and `<prefix>-N`.

When a preset and a live task describe the same command, the preset's name wins
and only one variant is generated.

Practical consequence: if `models` comes back `0`, check the file has presets or
live tasks. An empty `tasks` with only `removedTasks` maps to nothing, which is
not a wiring problem.

## Choose a source: the state file, or the API

**Prefer `odysseus.stateFile`.** Odysseus gates `/api/cookbook/state` behind
`require_admin` *and* an `AuthMiddleware` whose internal-tool bypass is
restricted to direct loopback clients:

```python
# app.py:385
if _hdr and secrets.compare_digest(_hdr, _ITT) and _is_trusted_loopback(request):
# _is_trusted_loopback: request.client.host must be 127.0.0.1 or ::1
```

That bypass exists for Odysseus's own in-process tool layer. A llama-swap in a
different container connects from the Docker network, so the branch is skipped and
the request falls through to the session-cookie check -- `401 Not authenticated`,
whatever the token says. The bearer-token path authenticates, but as the
synthetic user `"api"`, and `is_admin("api")` is false, so `require_admin` then
answers 403.

The way through is the file. Odysseus persists its state to
`DATA_DIR/cookbook_state.json`, which is `/app/data/cookbook_state.json` in the
container and `${APP_DATA_DIR}/cookbook_state.json` on the host. Mount that
directory read-only into llama-swap and point `odysseus.stateFile` at it:

```yaml
# llama-swap's compose
volumes:
  - /mnt/user/appdata/odysseus/data:/app/odysseus-data:ro
```

```yaml
odysseus:
  enabled: true
  stateFile: "/app/odysseus-data/cookbook_state.json"
  refreshSeconds: 300
  profilePrefix: "ody"
  outputFile: "/app/config.d/odysseus.generated.yaml"
  ensureDio: true
```

No credential, no network call, no middleware. `baseURL` and the token are
unused when `stateFile` is set, and config validation does not require them.

The API source still works where the caller *is* loopback -- `odysseus.baseURL`
plus `odysseus.token`.

## Authentication

Odysseus gates `/api/cookbook/state` behind `require_admin`, which accepts the
header `X-Odysseus-Internal-Token` — the mechanism its own tool layer uses
internally (`core/middleware.py`).

The value is `ODYSSEUS_INTERNAL_TOKEN` **from the Odysseus container's
environment**. If that variable is unset, Odysseus generates a random token per
process and never persists or exposes it, so it cannot be read from outside. Set
it explicitly on the Odysseus container, then pass the same value to llama-swap:

```bash
# Odysseus container
ODYSSEUS_INTERNAL_TOKEN=<a long random string>

# llama-swap's deploy/.env
ODYSSEUS_INTERNAL_TOKEN=<the same value>
```

`token: "${env.ODYSSEUS_INTERNAL_TOKEN}"` substitutes it at config load, so the
secret stays out of `config.yaml`. `odysseus.tokenEnv` and `odysseus.tokenFile`
read it from the environment or a mounted file at refresh time instead.

## When it refreshes

| Trigger | Behaviour |
|---|---|
| Startup | one refresh when the server is built |
| Timer | every `odysseus.refreshSeconds`; `0` disables it |
| `GET /v1/models` | background refresh, coalesced so a burst of listings causes at most one call per 30s |
| Manual | `POST /api/odysseus/refresh` — synchronous, returns counts |

`GET /api/odysseus/status` reports the configuration and the last run without
triggering one. Reusing `GET /v1/models` means every OpenAI-compatible client
keeps the profiles current for free; the coalescing stops a polling client from
turning that into traffic.

A refresh only writes when the content changed. That matters: llama-swap reloads
on any change under `-config-dir`, and a reload restarts every running model, so a
no-op refresh must not touch the file.

## Duplicate keys

`models` and `profiles` are identity-keyed during the config-dir merge, so the
same key in two files is a hard error. Generated keys are namespaced
(`<base>--<prefix>`, and profile names `<prefix>` / `<prefix>-N`), and before
writing, the integration reads every other config source and refuses if a key it
would define already exists — naming the key and the file. If that fires, change
`odysseus.profilePrefix`, or remove the conflicting entry.

## Commands are translated, not run as-is

llama-swap executes the command directly, with no shell, so two shell constructs
have to be rewritten before they can work:

| Odysseus writes | Becomes |
|---|---|
| `export HIP_VISIBLE_DEVICES=0` | the model's `env` list — `export` is not a program |
| `--model "$(printf %s '/p')"` | `--model /p` — command substitution is never evaluated |
| `--port 8000` | `--port ${PORT}` — llama-swap allocates one port per model |

`odysseus.ensureDio` (default on) also injects `-lm dio` into any command that has
no load mode, because `FEATURES.md` requires it: mmap on the model file hangs on
this stack. `--mmproj` and `--image-max-tokens` pass through untouched, so
multimodal models keep their projector.

Because the `export` lines become that model's `env` list, per-model variables
are the right place for launch tuning that varies by model --
`HIP_VISIBLE_DEVICES`, `GGML_ENABLE_CUSTOM_AR`, `HSA_FORCE_FINE_GRAIN_PCIE`,
`LLAMA_ENABLE_MTP_OPT`, `GPU_MAX_HW_QUEUES` and so on. Do **not** set those in the
container's `environment:`; that applies them to every model, including the ones
llama-swap launches with its own defaults. The only variable llama-swap itself
needs in the container environment is the Odysseus token.

Precedence works in the per-model direction: llama-swap spawns a model with
`append(cmd.Environ(), model.Env...)`, so a per-model entry is appended after the
container's and wins for the same key.

Tasks that are not llama.cpp (a vLLM command, say) have no `.gguf` path to map, so
they are skipped with a warning rather than guessed at.

## Related

- `guides/routing/profiles-and-selectors` — how profiles and pins resolve
- `guides/routing/groups-and-matrix` — which models can be loaded at the same time
