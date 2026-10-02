# GPU toggles — logic for the llama-swap detail-view switches

The decision logic behind the per-model switches in the model detail view. A
model is in exactly ONE mode:

- **Standard** (default) — the opt-out. Use the effective launch exactly as
  written; neither toggle applies. This is the switch for a model that is not
  meant to use either toggle (deliberately pinned, an embedding model, a
  one-off experiment).
- **Single GPU** — do not treat `HIP_VISIBLE_DEVICES=0,1` as the source of
  truth. At launch, bind the model to whichever card actually has enough free
  VRAM. A hard pin (`=0` / `=1`) is honoured only while that card still fits;
  otherwise the emptiest fitting card wins.
- **Multi-Model** — run the same model as several instances so each instance
  owns exactly its `--parallel` worth of chats. `instances = ceil(chats /
  parallel)`, one instance per GPU. When no card has room, the overflow chats
  queue and drain as slots free.

All read the model's **effective launch** — the resolved `cmd` + `env` the
detail card already renders.

## Files

| file | role |
|---|---|
| `mm_engine.py` | pure decision logic: parse launch, single-GPU pick, instance scaling |
| `mm_proxy.py` | the mechanism: `Pool` hands out a backend per chat, spawning one `llama-server` per GPU |
| `mm_toggles.py` | per-model toggle state (atomic JSON, like the odysseus choices file) |
| `pick_gpu.py` | GPU/VRAM picker used by the single-GPU path |
| `llama_gpu_launch.sh` | launch wrapper: asks `pick_gpu`, sets `HIP_VISIBLE_DEVICES`, execs |
| `llama_server.py` | strips bookkeeping flags and `execvp`s `llama-server` (exec, not spawn — a child would be orphaned on unload and leak VRAM) |

## Reading the effective launch

```
HIP_VISIBLE_DEVICES  <- env entries ("0", "1", "0,1", or absent)
--parallel N         <- the command (also --parallel=N and -np N)
```

## Logic

```
resolve_mode(toggles) -> "standard" | "single_gpu" | "multi_model"
    # "mode" if set, else derived from a legacy single_gpu/multi_model pair;
    # default "standard"

# standard   -> return the effective launch unchanged (device = the pin as-is)
# single_gpu -> single_gpu_pick(...) below
# multi_model-> plan_multi_model(...) below

single_gpu_pick(free, model_gb, margin, prefer=pin):
    needed  = model_gb + margin
    fitting = [gpu for gpu if free[gpu] >= needed]
    return prefer if prefer in fitting else emptiest(fitting)   # None if empty

instances_needed(chats, parallel) = ceil(chats / parallel)

plan_multi_model(chats, parallel, free, model_gb, margin, existing):
    need  = instances_needed(chats, parallel)
    start = emptiest_fitting_gpus[: need - existing]
    capacity = (existing + len(start)) * parallel
    queued   = max(0, chats - capacity)
```

## Tests

```
python3 test_mm_engine.py     # 31
python3 test_mm_pool.py       # 10
python3 test_mm_toggles.py    #  8
python3 test_pick_gpu.py      #  9
```

All are pure-logic; they run on a host with no GPU (fake sysfs + fake start).

## Wiring (not done yet)

The toggles still need to be exposed in llama-swap itself:

1. **state** — load a `togglesPath` beside `choicesPath` on startup.
2. **API** — `PUT /api/models/{model}/toggles` `{"mode": "standard" | "single_gpu" | "multi_model"}`.
3. **UI** — a one-of-three mode control in
   `ui/src/components/model/ModelDetailsTab.svelte` (Standard / Single GPU /
   Multi-Model), so a model that is not meant to use either toggle can be left
   on **Standard**.
4. **launch** — `single_gpu` routes the cmd through `llama_gpu_launch.sh`;
   `multi_model` makes the cmd `mm_proxy` (whose HTTP forwarding layer,
   `serve()`, is still a stub — the scheduling core is complete and tested);
   `standard` leaves the effective launch untouched.
