---
title: Choosing GPUs and context in the Builder, and stretching it with RoPE
summary: The Builder's GPUs field saves card numbers and Argos sets HIP or CUDA for where the model runs; its VRAM card sets context from the KV table and can stretch a model to 1M with YaRN.
category: guides
tags: [gpu, hip_visible_devices, cuda_visible_devices, context, rope, yarn, vram, builder]
updated: 2026-10-25
---

# Choosing GPUs in the Builder

The Builder's settings have a **GPUs** field next to Context and GPU layers:
**All** (no pin) and a chip per card Argos sees. Click cards to pick them, or type
the list (`0,1`, numbered from 0 the way HIP and CUDA count them).

Only the card numbers are saved, as `ARGOS_GPUS=0,1` on the profile. Which
variable the GPU runtime reads depends on where the model runs, so Argos sets it
when the model launches or is handed to the engine:

| runs on | gets |
| --- | --- |
| a remote launcher (`/app/bin/remote-llama rocm`) | that backend's variable: `HIP_VISIBLE_DEVICES`, `CUDA_VISIBLE_DEVICES` or `GGML_VK_VISIBLE_DEVICES` |
| this machine with ROCm (`/dev/kfd` or `rocm-smi`) | `HIP_VISIBLE_DEVICES` |
| this machine with NVIDIA (`/dev/nvidiactl` or `nvidia-smi`) | `CUDA_VISIBLE_DEVICES` |
| both, or neither found | both |

The line under the field shows what this command will launch as.
`ARGOS_GPUS` itself never reaches the process.

A command that sets one of those variables by hand keeps it: the field shows its
cards and says so, and picking cards in the field replaces it with `ARGOS_GPUS`.

# Context from the VRAM card

The Builder's **VRAM** card has a table of the KV cache alone, by cache type and
context. Click a cell to use that context and that cache type (`-c`, `-ctk`,
`-ctv`); click a column heading to change only the context.

# Stretching to 1M with RoPE

For a model trained on less than 1M tokens the table has a last column, **1M
RoPE**, and the card has a **RoPE (YaRN) to 1M context** switch. Turning it on
writes, for a model trained on 256K:

```
-c 1048576 --rope-scaling yarn --rope-scale 4 --yarn-orig-ctx 262144
```

The scale is 1M divided by the trained context. Turning it off removes the three
RoPE flags and sets the context back to the trained one. Clicking a context at or
under the trained one also removes them.

What goes wrong:

- The KV cache grows with the context: at 1M it is four times the 256K column.
  Check the total against your cards before saving.
- Quality drops at long range when a model is stretched past what it was trained
  on. Some models document their own YaRN settings; when they do, use those.
- Without the RoPE flags, a context past the trained one is flagged on the card:
  the model will not cope with it.
