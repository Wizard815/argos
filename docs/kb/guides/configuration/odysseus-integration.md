---
title: Odysseus integration
summary: Let Odysseus own a model's launch command and switch to it with a profile, without changing Argos's defaults.
category: guides
tags: [odysseus, profiles, integration, launch, commands, cookbook]
config_keys: [odysseus, odysseus.enabled, odysseus.stateFile, odysseus.baseURL, odysseus.token, odysseus.tokenEnv, odysseus.tokenFile, odysseus.refreshSeconds, odysseus.profilePrefix, odysseus.statuses, odysseus.outputFile, odysseus.ensureDio, odysseus.listVariants, odysseus.timeoutSeconds]
updated: 2026-10-26
---

# Odysseus integration

Odysseus' Cookbook assembles a complete `llama-server ...` command in the browser
and runs it in tmux, keeping the command it used in `cookbook_state.json`
(`task.payload._cmd`). This integration lets that command drive Argos, so
Odysseus stays the author of a model's flags while Argos stays the process
supervisor.

Turn it on with `odysseus.enabled`. Argos polls Odysseus, and for each model
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
  listVariants: true                         # profiles show up in /v1/models
```

`outputFile` must live in the `-config-dir` so Argos's merge and the
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
and only one variant is generated. "The same command" means the same flags and
the same environment: two presets with identical flags but `HIP_VISIBLE_DEVICES=0`
and `=1` are two variants, each on its own card.

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

That bypass exists for Odysseus's own in-process tool layer. An Argos in a
different container connects from the Docker network, so the branch is skipped and
the request falls through to the session-cookie check -- `401 Not authenticated`,
whatever the token says. The bearer-token path authenticates, but as the
synthetic user `"api"`, and `is_admin("api")` is false, so `require_admin` then
answers 403.

The way through is the file. Odysseus persists its state to
`DATA_DIR/cookbook_state.json`, which is `/app/data/cookbook_state.json` in the
container and `${APP_DATA_DIR}/cookbook_state.json` on the host. Mount that
directory read-only into Argos and point `odysseus.stateFile` at it:

```yaml
# Argos's compose
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
it explicitly on the Odysseus container, then pass the same value to Argos:

```bash
# Odysseus container
ODYSSEUS_INTERNAL_TOKEN=<a long random string>

# Argos's deploy/.env
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

A refresh only writes when the content changed. That matters: Argos reloads
on any change under `-config-dir`. A reload restarts only the models whose
configuration changed and carries the rest over untouched, but an unnecessary
write still costs every model the refresh rewrote - so a no-op refresh must not
touch the file at all.

## Duplicate keys

`models` and `profiles` are identity-keyed during the config-dir merge, so the
same key in two files is a hard error. Generated keys are namespaced
(`<base>--<prefix>`, and profile names `<prefix>` / `<prefix>-N`), and before
writing, the integration reads every other config source and refuses if a key it
would define already exists — naming the key and the file. If that fires, change
`odysseus.profilePrefix`, or remove the conflicting entry.

## Commands are translated, not run as-is

Argos executes the command directly, with no shell, so two shell constructs
have to be rewritten before they can work:

| Odysseus writes | Becomes |
|---|---|
| `export HIP_VISIBLE_DEVICES=0` | the model's `env` list — `export` is not a program |
| `--model "$(printf %s '/p')"` | `--model /p` — command substitution is never evaluated |
| `--port 8000` | `--port ${PORT}` — Argos allocates one port per model |

`odysseus.ensureDio` (default on) also injects `-lm dio` into any command that has
no load mode, because `FEATURES.md` requires it: mmap on the model file hangs on
this stack. `--mmproj` and `--image-max-tokens` pass through untouched, so
multimodal models keep their projector.

Because the `export` lines become that model's `env` list, per-model variables
are the right place for launch tuning that varies by model --
`HIP_VISIBLE_DEVICES`, `GGML_ENABLE_CUSTOM_AR`, `HSA_FORCE_FINE_GRAIN_PCIE`,
`LLAMA_ENABLE_MTP_OPT`, `GPU_MAX_HW_QUEUES` and so on. Do **not** set those in the
container's `environment:`; that applies them to every model, including the ones
Argos launches with its own defaults. The only variable Argos itself
needs in the container environment is the Odysseus token.

Precedence works in the per-model direction: Argos spawns a model with
`append(cmd.Environ(), model.Env...)`, so a per-model entry is appended after the
container's and wins for the same key.

Tasks that are not llama.cpp (a vLLM command, say) have no `.gguf` path to map, so
they are skipped with a warning rather than guessed at.

## The program a variant runs

A preset that starts with a bare `llama-server` runs with the program its base
model starts with: whatever the model's own `cmd` has before its first flag, such
as `/app/bin/remote-llama rocm` from a `${bk-rocm}` macro, or a full path to a
llama-server binary. So the Argos container needs no `llama-server` of its own
when llama.cpp runs elsewhere; the variant goes the same way the base model does.
A preset that names another program keeps it.

## Which server profiles run on

The **Server** select at the top of the Profiles page lists Local and the SSH
servers from Servers, like the server select in Odysseus' Cookbook. It is the
default server.

- **Local:** a plain `llama-server` profile runs the way the base models do
  (the section above).
- **An SSH server:** a plain `llama-server` profile runs there, through the
  launcher with that server's own settings:

  ```
  ROCM_SRV3_SSH_HOST=mx-llamacpp-ssh ROCM_SRV3_SSH_USER=root ... /app/bin/remote-llama rocm_srv3 --model ...
  ```

  The name carries the server's GPU runtime (its GPU setting, or what Check
  found), so the GPUs field writes `HIP_VISIBLE_DEVICES` or
  `CUDA_VISIBLE_DEVICES` to match. Its llama-server path and its key (when
  Servers made one) go along too.

A profile that names its own program keeps it. Changing the server asks the
engine to re-read the profiles; models already running stay where they are
until they are loaded again.

## Which profile a client gets

A client asking for a model by its own name (`"model": "ornith-1.5-9b-q6_k"`,
from Hermes, Open WebUI, any OpenAI client) gets that model's default command.
A profile is the variant `<model>--<label>`. Two ways to get it:

- **Serve (the model's default):** the **Model defaults** card on the Profiles
  page, **Make default** in the Builder, or **serve** on a profile makes
  requests for
  the model's plain name run that profile. One profile per model; **serving**
  marks it, and clicking it again goes back to the model's own command. This
  is the model's choice (`PUT /api/odysseus/model/{model}/profile`), and it
  routes requests by itself: no profile has to be activated, and it survives a
  restart. An active profile's pin still wins where it has one.
  It applies to the model's aliases too, and `/v1/models` names the plain model
  `<name> (default: <label>)` so a client's picker shows what it gets.
- **Pick the profile in the client:** every saved profile is listed in
  `/v1/models` as `<model>--<label>`, named `<model name> (<label>)`, so
  Hermes' "Set Session Model" (or any model picker) shows it next to the plain
  model. Choosing it runs that profile, whatever the model serves by default.
  The **Served as** column shows the same name, with a copy button.

`odysseus.listVariants: false` takes the profiles back out of `/v1/models`
(default `true`). They still answer when asked for by name, and a served
profile still applies.

To skip refreshing a client's model list for every new profile, select the
plain model in the client once and change its default in Argos: the client's
name stays the same, and the next request runs the new default.

What goes wrong:

- **The client shows only plain models:** the engine predates this setting, or
  `listVariants` is `false`. Some clients cache the model list; refresh it.
- **A profile is missing:** only saved profiles are exported. Save it; the
  engine picks it up on its next refresh (`refreshSeconds`).

## Launching a profile from the Builder

The base model keeps its own default command, so loading it ignores every
profile. **Launch** in the Builder (and on the Profiles page) loads the profile's
variant instead, so the engine runs exactly the saved command: its context, cache
types, split mode and GPU pin. If the profile was saved a moment ago, Launch asks
the engine to refresh and waits up to 10 seconds for the variant.

What goes wrong:

- **Unsaved changes:** Launch refuses and says so. Save (or Save as new) first;
  the engine only runs saved profiles.
- **"The engine does not have this profile":** the engine never picked it up.
  Check `odysseus.baseURL` points at the builder.
- **A pasted command that is not a profile:** the engine loads the base model with
  its own command and the Builder warns that your settings were not used.

## Related

- `guides/routing/profiles-and-selectors` — how profiles and pins resolve
- `guides/routing/groups-and-matrix` — which models can be loaded at the same time
