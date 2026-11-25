---
title: MoE offload for every profile of a model
summary: Keep part of a mixture-of-experts model's expert weights in system RAM for all its profiles at once, from Profiles -> MoE offload, with a chart of GPU and RAM per profile.
category: guides
tags: [moe, mixture-of-experts, n-cpu-moe, cpu-moe, offload, vram, ram, profiles, experts]
updated: 2026-11-25
---

# MoE offload for every profile of a model

A mixture-of-experts model (Qwen3 MoE, GLM, gpt-oss, DeepSeek...) can keep
some of its expert weights in system RAM: llama-server's `--n-cpu-moe N`
keeps the experts of the first N layers there, `--cpu-moe` all of them. That
frees VRAM, so the model fits beside others, for some speed.

**Profiles -> MoE offload** sets it once per model file:

- **The slider** is the share of the model's MoE layers whose experts go to
  RAM. Argos turns it into `--n-cpu-moe N` (100% is `--cpu-moe`), counting
  only MoE layers, so a model whose first layers are dense still moves the
  share you asked for.
- **Default** leaves every profile with its own setting.
- **Exempt** a profile to keep its own command for it.
- **The chart** shows each profile's GPU and RAM share as you drag: the
  expert sizes are read from the model file's tensor table, not guessed.

Apply writes it into the commands Argos hands the engine. Your saved profiles
do not change, and the Schedule plans with the GPU share only.

## What goes wrong

- **The model restarts.** llama-server cannot move experts while it runs: a
  running model reloads with the new setting (quickly when the file is still
  in the page cache).
- **It got slower.** Experts in RAM run on the CPU. Measure the profile to see
  by how much; the GPU share is what the slider buys.
- **"Needs up to N GiB of RAM"** on the chart: the RAM share is more than this
  machine has free. Lower the slider, or the model swaps to disk.
- **A model is missing from the page.** Only MoE models with a profile are
  listed; a dense model has no experts to move.
