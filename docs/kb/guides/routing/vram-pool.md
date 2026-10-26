---
title: VRAM-aware pool router
summary: Put every model in one pool so models load side by side while VRAM fits, and only the cheapest idle model is evicted when it does not.
category: guides
tags: [routing, pool, vram, eviction, swap, gpu, memory, persistent]
config_keys: [routing, models.*.vram]
updated: 2026-10-25
---

# VRAM-aware pool router

With the default `group` router every model is in one swapping group, so
loading any model unloads the others, even a 0.6B model next to a 30B one
with plenty of free VRAM. The `pool` router fixes that: models share the
GPUs while they fit.

```yaml
models:
  qwen-30b:
    cmd: llama-server --port ${PORT} -m /models/qwen3-30b-q4.gguf -c 32768
    vram: 26          # weights + KV cache for -c 32768
  qwen-0.6b:
    cmd: llama-server --port ${PORT} -m /models/qwen3-0.6b.gguf
  whisper:
    cmd: whisper-server --port ${PORT} -m /models/ggml-large-v3.bin
    vram: 4

routing:
  router:
    use: pool
    settings:
      pool:
        cards: [32, 32]   # empty = detect the cards at startup
        reserveGiB: 2
        protectSeconds: 600
        persistent: [whisper]
```

## How it decides

1. If the requested model fits next to everything running, nothing stops.
2. Otherwise running models are ranked: idle first, then models used in the
   last `protectSeconds`, busy models last. Within each, the model that is
   quickest to reload and idle longest goes first. Load times are measured.
3. Models are taken in that order until the new one fits. Any that turn out
   not to be needed are put back.
4. `persistent` models are never evicted for another model.

## Per-card placement

With per-card sizes (`cards`, or detection when `vramGiB` is 0) the pool also
chooses **which card** each model runs on, and sets `HIP_VISIBLE_DEVICES` when
the model starts:

- A model that fits on one card goes on one card. Of the cards with room it
  picks the one with the fewest models, then the most free VRAM, so the cards
  share the work and each model gets a whole card's compute and bandwidth.
- A model bigger than a card is split over the cards with the most room.
- Fragmentation counts: 22 GiB free in total is no use for a 28 GiB model when
  neither card has 28 free, so the pool evicts until one card does.
- A `HIP_VISIBLE_DEVICES` in a model's `env` limits the cards it may use. A
  pin to more than one card (`HIP_VISIBLE_DEVICES=0,1`) means use all of them.
- A `cmd` with `--split-mode tensor` or `row` (`-sm`) is spread the same way over
  every card it may use, since those modes exist to share each layer across
  GPUs. `layer` (llama-server's default) and `none` still fit on one card.
- How much room that needs depends on the mode, because llama.cpp picks the cut
  points differently: with no `--tensor-split`, `tensor` divides the model
  evenly, so every card it uses has to hold its exact share, while `layer` and
  `row` split in proportion to each card's free memory, so what has to cover the
  model is the cards' free space together.
- A model too big for one card is placed on the cards with the most room, and
  again together they have to cover it - not half each, which turns away a model
  they can hold as soon as one card is a little tighter than the other.
- Card ids come from `rocm-smi`, the numbering `HIP_VISIBLE_DEVICES` uses. If
  `rocm-smi` is missing the pool falls back to `/sys/class/drm`, numbers the
  cards that have VRAM 0, 1, ... in that order (HIP's usual order, whatever
  the DRM `cardN` numbers are) and warns. Set `cards` to be sure.
- Set `placement: off` to plan by total VRAM only.

Do not hard-code a card split such as `-ts 1,1` or `--device` in a model's
`cmd` when the pool may put it on one card; the pool decides the cards.

## Model sizes

Set `models.*.vram` in GiB, including the KV cache for your context size.
Or put the sizes in the pool block, which lets a separate file in
`-config-dir` supply them without redefining any model:

```yaml
routing:
  router:
    use: pool
    settings:
      pool:
        cards: [32, 32]
        sizes: {qwen3-30b: 22.5, whisper: 4}
```

`models.*.vram` wins over `pool.sizes`, and either wins over the estimate.
Without it the size is estimated from the `-m` file (weights + 20% + 1 GiB;
split GGUFs are summed). A model with no `vram` and no `-m` file has an
unknown size and runs alone, like the group default. The log warns about each
one at startup.

## Watching it: GET /api/pool

`GET /api/pool` reports what the pool is doing right now: each card's planned
use and the models on it, every model's state, size and card, the requests
queued (with how long each has waited) and the models loading. With any other
router it answers `{"enabled": false}`. Sizes there are the pool's plan, not live
VRAM; the Performance page shows the real memory.

The builder's **Schedule -> Live** shows this next to a **Requests** feed: the
engine's newest requests (chat, embeddings, benchmarks, audio), who sent them,
the context in and out, speeds and status, and how many each model is working
on now. **Usage -> GPU** has the same feed under its charts with **Recent
requests** ticked. Only requests through the engine appear; a client talking to
a llama-server port directly bypasses it.

## What goes wrong

- **Out of memory on load:** a `vram` value is too low, or the estimate missed
  a large context. Raise `vram` or `reserveGiB`.
- **Models still swap out:** check the startup log for "unknown VRAM size",
  and for "could not detect VRAM" (set `cards` or `vramGiB`).
- **A model lands on the wrong card:** the log shows "gpu placement: binding
  ..." for every start. Check the ids against `rocm-smi`.
- **A model unloads after a few minutes:** that is its `ttl`, not the pool.
  See `guides/model-runtime/ttl-and-unloading`.
