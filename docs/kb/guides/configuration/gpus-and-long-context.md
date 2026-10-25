---
title: Choosing GPUs for every model, and stretching context with RoPE
summary: Settings -> GPUs pins every model to chosen cards without a per-profile env var; the Builder's VRAM card sets context from its table and can stretch a model to 1M with YaRN.
category: guides
tags: [gpu, hip_visible_devices, cuda_visible_devices, context, rope, yarn, vram, builder, settings]
updated: 2026-10-25
---

# Choosing GPUs for every model

**Settings -> GPUs -> Visible GPUs** takes the cards models may use, numbered from
0 the way HIP and CUDA count them (`0,1`). Blank means all cards. The chips under
the field are the cards Argos sees; click one to add or remove it.

It is added to every model the engine starts and every launch from the Builder,
so a profile no longer needs `HIP_VISIBLE_DEVICES` in its environment. It follows
**Always set**'s two switches (launches here, models the engine starts).

**Set it as** picks the variable:

| choice | sets |
| --- | --- |
| Auto | `HIP_VISIBLE_DEVICES` and `CUDA_VISIBLE_DEVICES` |
| HIP_VISIBLE_DEVICES | AMD |
| ROCR_VISIBLE_DEVICES | AMD inside containers |
| CUDA_VISIBLE_DEVICES | NVIDIA |

A command that already picks its cards keeps them: if a profile (or **Always
set**'s environment) sets any of `HIP_VISIBLE_DEVICES`, `ROCR_VISIBLE_DEVICES`,
`CUDA_VISIBLE_DEVICES`, `GPU_DEVICE_ORDINAL` or `GGML_VK_VISIBLE_DEVICES`, this
setting adds nothing to it. So pin one model to card 1 in its profile and leave
the rest to Settings.

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
