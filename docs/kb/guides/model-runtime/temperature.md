---
title: Temperature per model, and measuring it
summary: Set a profile's temperature (and top-p, top-k, min-p) without a reload, force it over what apps send, and chart right answers and variety against temperature to pick one.
category: guides
tags: [temperature, sampling, top-p, min-p, profiles, accuracy, creativity, sweep, codex, gemma]
updated: 2026-11-25
---

# Temperature per model, and measuring it

Profiles -> Temperature. Temperature is how much a model gambles on each
word: at 0 it always takes the likeliest one, the higher it goes the more it
takes others. Higher gives more varied writing, and more slips. In one coding
test, Gemma passed 87 of 92 hidden checks at 0.2, 85 at 0.6 and 52 at 1.0,
with two of its four programs broken at 1.0.

## Set it per profile

| Use a temperature | what happens |
| --- | --- |
| as the app sends | nothing: the app's own, else the model's default |
| when the app sends none | the profile's temperature fills in what the app left out |
| always | it replaces what the app sends (many send 1.0 whatever the model) |

Argos sets it on each request on its way to the model, so the profile's command
does not change and nothing reloads. The answer carries `X-Argos-Sampling`.
Top-p, top-k and min-p work the same way.

## A sweep

**Run a sweep** asks the loaded model, at each temperature:

- 16 questions with one right answer (arithmetic, what a few lines of
  Python print, dates, number sequences): **right answers**, the smart line;
- one short story four times: **variety**, how different the stories are from
  each other (0% when they are word for word the same).

The chart draws both against temperature. **best** is the hottest temperature
still near the most right answers whose next hotter one is near them too, so
there is room before the answers fall; **Use** sets it as the profile's
temperature ("always"). Seven temperatures take a few minutes; chats on that
model wait their turn meanwhile.

**Add my own results** draws results measured elsewhere, one temperature a
line: `0.2: 87/92, 0 broken`.

## What goes wrong

- **Many answers "cut off while thinking".** A thinking model spent all its
  tokens thinking; it thinks longer the hotter it runs. They count as wrong.
  Untick "Let it think first", or give the model a template without thinking.
- **Variety "not measured".** Fewer than two stories came back.
- **The temperature does nothing.** "as the app sends" is set, or the app
  sends its own and the mode is "when the app sends none": use "always".
