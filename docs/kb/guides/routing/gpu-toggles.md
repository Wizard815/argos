---
title: Per-model GPU toggles in the model detail view
summary: modelTogglesFile remembers each model's GPU mode — standard, single GPU, or multi-model — set from the detail view.
category: guides
tags: [gpu, vram, parallel, multi-model, toggles, detail-view, placement]
config_keys: [modelTogglesFile]
updated: 2026-10-02
---

# Per-model GPU toggles

Each model has one **GPU mode**, chosen in the model detail view and remembered
in `modelTogglesFile`. The mode decides how the model's effective launch is
placed across GPUs.

```yaml
modelTogglesFile: /app/config-dir/model_toggles.json
```

The file must live in the `-config-dir`, so it is rewritten in place and
survives a reload or a container recreate. An empty `modelTogglesFile` (the
default) hides the control and leaves every model on `standard`.

## The three modes

| mode | what it does |
| --- | --- |
| `standard` | **The default, and the opt-out.** The effective launch runs exactly as written — no GPU pick, no scale-out. Use it for a model that is not meant to use either toggle. |
| `single_gpu` | Do not trust `HIP_VISIBLE_DEVICES=0,1`. At launch, bind to whichever card actually has enough free VRAM. A hard pin (`=0` / `=1`) is honoured only while that card still fits; otherwise the emptiest fitting card wins. |
| `multi_model` | Run the model as several instances, so each instance owns its `--parallel` worth of chats: `instances = ceil(chats / parallel)`, one instance per GPU. When no card has room, the overflow queues and drains as slots free. |

`standard` is the switch for the case you asked about: a model that is not meant
to use either toggle. It is the default, so a model is only moved or scaled once
you say so.

## How a mode is chosen

The mode is read from the model's **effective launch** — the resolved `cmd` and
`env` the detail view already shows:

- `HIP_VISIBLE_DEVICES` comes from the model's `env` entries
- `--parallel N` comes from the command (`--parallel=N` and `-np N` also work)

So `single_gpu` needs no new config on the model; it only changes which card the
existing command binds to.

`single_gpu` is applied **at launch**: just before the upstream starts, its
`HIP_VISIBLE_DEVICES` is rewritten to the chosen card, so a mode change takes
effect the next time the model loads. It reads free VRAM from
`/sys/class/drm/*/device/mem_info_vram_free`, takes the weight size from the
command's `-m`, and leaves a 4 GiB margin for the KV cache. If the pinned card
no longer fits, the emptiest fitting card wins. If *no* card fits, it binds the
emptiest one anyway and logs a warning — the same OOM you would get by pinning a
card that is too small.

`multi_model` expands the model at load time into one copy per ROCm card and
fronts them with a spillover selector, so one id serves
`ceil(chats / parallel)` instances: the first copy fills to `--parallel`
concurrent chats, then the next copy is started on demand. Copies are named
`<id>--mm<N>`, are unlisted, each prefers a different card, and live in a
non-exclusive `multimodel` group so they coexist.

The base model **stays configured and listed** — the selector sits over it, so
the model still appears in the UI and its mode can still be changed. Requests
for the base id are rewritten to a copy before the router runs, so the base's
own process never starts.

Two things to know:

- The expansion happens when the config loads, and the watcher only watches
  `*.yml`/`*.yaml`, so switching a model to `multi_model` takes effect on the
  next config reload — restart llama-swap (or touch a config file).
- A profile pin normally rewrites the model id *before* selectors resolve, which
  would bypass the selector. A `multi_model` model therefore **declines its pin**
  — the selector is used instead, and turning the mode off restores the pin.
- Free VRAM is read from `mem_info_vram_free`, falling back to
  `total - used` on kernels that do not expose it. If neither is readable the
  expansion still runs off the card nodes, and the launch policy skips its fit
  test.

## Reading and setting it

```console
$ curl -s localhost:8080/api/models/modes
{"ok":true,"modes":{"my-35b":"single_gpu"},"valid":["standard","single_gpu","multi_model"],"enabled":true}

$ curl -s -X PUT localhost:8080/api/models/my-35b/mode \
    -H 'Content-Type: application/json' -d '{"mode":"multi_model"}'
{"ok":true,"model":"my-35b","mode":"multi_model"}
```

Each model's `mode` also rides along on the model list (`/v1/models` and
`/api/models`), omitted when `standard`.

## What goes wrong

- **`multi_model` with a model that fills a whole card.** The instance count is
  capped by free VRAM, not by `ceil(chats/parallel)`. A 35B Q6_K (~27 GiB) fits
  one 32 GiB card, so a third chat queues rather than starting a third instance.
- **`single_gpu` when no card fits.** The launch fails rather than binding to a
  full card — that is deliberate. Lower the context, or use a smaller quant.
- **A `modelTogglesFile` outside the `-config-dir`.** The file still works, but a
  container recreate loses it and the modes silently revert to `standard`.
- **Mode stored, but the launch still pins `0,1`.** Only `single_gpu` and
  `multi_model` rewrite the device; `standard` never touches the command.
