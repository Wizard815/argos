---
title: GPU power profiles and tokens per watt
summary: Give each profile a GPU power cap and top clock from Profiles -> Power, applied while it runs, and compare benchmarks by watts and tokens per watt.
category: guides
tags: [power, power-cap, watts, efficiency, tokens-per-watt, mi50, gfx906, rocm-smi, amd-smi, sclk, fan-bridge, temperature, benchmark]
updated: 2026-11-25
---

# GPU power profiles and tokens per watt

**Profiles -> Power** gives each profile a power cap (and, if you want, a top
core clock). While the profile runs, Argos sets its GPUs to it. Your saved
launch commands do not change: llama-server cannot set a power cap.

- **The switch** at the top turns it all off. The cards go back to stock once,
  then Argos leaves them alone.
- **Each profile** has a preset (Stock, Balanced 180 W, Efficient 150 W,
  Eco 125 W) or a custom cap, and a top clock ("all clocks" = no limit).
  Untick **on** to stop using it; the values are kept for next time.
- **Two running profiles** with different settings get the higher cap and the
  higher clock, so neither is slowed by the other.
- **While nothing with a setting runs**: the idle setting, if you set one.
  Without one, a card Argos changed goes back to stock; a card it never
  touched is left as it is.
- **The GPUs card** shows each card's cap, its allowed range, its watts,
  clock and temperatures. With fan-bridge set, the temperatures and the fan
  floor come from the fan monitor.

On an MI50, generation is memory-bound: a lower cap costs little there and
mostly slows prompt reading. Measure it rather than guess.

## Compare settings

Playground -> llama-bench -> **GPU power** runs a benchmark at a chosen
setting (or the profile's own). Every result records the GPUs' average watts
while that test ran, its tokens per watt (tokens per joule) and the setting.
Benchmarks shows them in the **Watts · t/W** column; **Compare** on the Power
page lists each profile's best speed per setting, most tokens per watt first.

The watts come from the GPU telemetry (Settings -> telemetry must be on).
Only the GPUs that were busy count, and only readings while they were, so a
card idling beside it and the model's load time do not flatter the number.

## How Argos writes it

Settings -> GPU power -> **Write through** (`backend`):

| backend | needs |
| --- | --- |
| `fan-bridge` | `fan_bridge_url`, e.g. `http://<host>:5000` (fan-bridge is not on `ai_net` by default). It already mounts the GPUs' sysfs writable. |
| `sysfs` | `/sys/class/drm/card*/device` writable inside Argos |
| `rocm-smi` | a rocm-smi that can write (root): `--setpoweroverdrive`, `--setsclk` |
| `amd-smi` | `amd-smi set --power-cap`; clock limits are not set this way |

`auto` (the default) takes fan-bridge when its URL is set, then sysfs if it is
writable, then rocm-smi, then amd-smi.

## What goes wrong

- **"read only"** on the page: none of the above can write here. A Docker
  container sees `/sys` read-only; point Argos at fan-bridge.
- **"raise held, a GPU is at N °C"**: a higher cap (or lifting a clock limit)
  waits while any GPU's junction is at or above `guard_c` (95 °C). A cut is
  never held. Check the fans first.
- **"outside what it allows"**: the cap is past the card's own
  `power1_cap_min`/`power1_cap_max`, which the BIOS sets.
- **fan-bridge's perf-on-demand** and a clock limit both want
  `power_dpm_force_performance_level`. Through fan-bridge, a clock limit pauses
  perf-on-demand on that card until the limit is lifted. Through sysfs or
  rocm-smi, turn perf-on-demand off yourself.
- **No watts on a result**: GPU telemetry was off, or the test was shorter
  than one reading and the run's average was used instead.
