---
title: MTP heads and vision projectors as helper files
summary: How the Builder finds a model's separate MTP head or mmproj file, adds it to the command, and how to attach one by hand.
category: guides
tags: [mtp, speculative, draft, mmproj, vision, helpers, library]
updated: 2026-11-18
---

# MTP heads and vision projectors as helper files

Some models ship extra small GGUFs that only work together with the main
file:

- **Vision projector** (`mmproj-*.gguf`): added as `--mmproj <file>`.
- **MTP head** (`*-MTP-*.gguf`, a few MB to a few GB): the model's
  multi-token-prediction layers on their own. It is added as a draft model,
  so speculative decoding uses the model's own head instead of a generic
  small draft model.

Argos treats both as **helpers** of the main model. They are not listed as
models of their own: neither on the Models page nor in the engine's
`modelScan` list (`/v1/models`), so they do not inflate the model count.

## How a file is recognised

| file | rule |
| --- | --- |
| vision projector | `mmproj` in the file name, or `clip` as the architecture in its GGUF header |
| MTP head | `mtp` (or `nextn`) in the name **and** 1–4 layers in its GGUF header |

A full model with `mtp` in its name (MTP built into the file, 40+ layers)
stays a normal model of kind `mtp`.

## How a helper is paired with its model

1. One model and one helper in the same folder: they pair.
2. Otherwise by name, ignoring quant, shard and words like `mtp`, `draft`,
   `mmproj`: `Qwen3-Coder-Next-Q5_K_S.gguf` pairs with
   `Qwen3-Coder-Next-MTP-Q8_0.gguf`. A helper in the same folder wins.
3. An MTP head can be anywhere under the models folder, so a separate
   `mtp/` folder works if it is named like the model.

The Models page shows an **MTP head** or **mmproj** chip in the Files column
when a helper is attached.

## Attaching by hand

Models → ⋯ → **Helpers (vision, MTP)…** has a select per helper:
**Automatic**, **None**, or any matching file. The choice is saved in
`.argos-helpers.json` at the root of the models folder, so it survives a
rescan and a container rebuild.

## The command it adds

New profiles built from the model (the prefab) get the MTP head through two
templates in Settings → Prefab:

```text
MTP head arguments:   -md {file} --spec-type draft-mtp --spec-draft-n-max 3
MTP head environment: LLAMA_ENABLE_MTP_OPT=1
```

`{file}` becomes the head's path. Embedding, reranker and models that already
have MTP built in never get one.

## What goes wrong

- **The head shows up as its own model.** Its header has more than 4 layers
  or no `mtp` in its name; rename it, or attach it by hand.
- **llama-server rejects `--spec-type draft-mtp`.** Flag names differ
  between llama.cpp builds. Check `llama-server --help` for your build and
  change the template in Settings → Prefab.
- **Existing profiles do not change.** Helpers apply when a profile is built;
  rebuild it from the model, or add the flags in the profile yourself.
- **Mode 2 containers:** `LLAMA_ENABLE_MTP_OPT` is passed into the
  llama.cpp container by `docker-llama` along with the GPU variables.
