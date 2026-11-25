---
title: What apps see in the model list, and calling classifiers
summary: Apps see your profiles by their own names, not the base models on disk; how to show one anyway, and how to call a decision model at /v1/systemone.
category: guides
tags: [models, v1-models, profiles, aliases, classifier, decision, systemone, openjev]
config_keys: [modelTogglesFile]
updated: 2026-11-25
---

# What apps see in the model list

Argos finds every GGUF in the models folder (the **base models**). Apps such
as Hermes, Codex or your own code only see the models you **made**: the
profiles. A request that comes in through Argos's port (the builder adds
`X-Forwarded-For`) gets this list:

| model | listed for apps | an app may call it |
| --- | --- | --- |
| a profile | yes, by its **label** | yes, by its label or its full id |
| a base model | no | only if it has a default profile (the call runs that) |
| a base model shown by hand | yes | yes |
| a model written by hand in `config.yaml` | yes | yes |

A profile's full id is `<base model>--<label>`, for example:

```text
qwen3.8-27b-turbofcfusion-735-882-here-uncen-neo-coder-max-mtp-q8_0--qwen3.8-27b-uncen-mtp-2chat-512k
```

Apps see it as `qwen3.8-27b-uncen-mtp-2chat-512k`. If two base models have a
profile with the same label (`fast`), both keep their full ids, so neither
name is ambiguous.

## Changing what apps see

Models -> Library -> **What apps see** has two choices, saved in
`modelTogglesFile`:

| choice | options |
| --- | --- |
| names | **profiles by their own name** (`qwen3.8-27b-uncen-mtp-2chat-512k`), or **model--profile** (the full id) |
| models | **only profiles** (default), **profiles, plus models with no profile** (a model you have not made a profile for yet shows under its own name), or **profiles and every model** |

Either name works when an app calls a profile, so switching names does not
break an app that saved the other one. A proxy in front of Argos such as
LiteLLM keeps its copy of the list for a few minutes: restart it to see a
change at once.

## Grouped by family

The list apps get is sorted by family (Qwen, Gemma, GLM, gpt-oss...), local
models first and each cloud provider after them, and `owned_by` holds the
family or the provider. Tick **family/ in front (qwen/...)** under What apps
see and each local name starts with its family, `qwen/qwen3.8-27b-uncen-mtp-2chat-512k`:
Open WebUI, Cline and LiteLLM-style pickers show a group per prefix. The name
without the prefix keeps working, so a saved app setting does not break.
`?all=1` is the engine's own list, unsorted.

## Showing a base model anyway

Models → a model's ⋯ → **Show the base model to apps**. It is listed under its
own id and can be called. It is saved in `modelTogglesFile` with the GPU
modes, so it survives a restart. **Hide the base model from apps** undoes it.

## What goes wrong

- **An app says "model … is a base model: make a profile for it".** Make a
  profile (Models → Prefab → save), give it a default, or show the base model.
- **An old app setting still uses the long id.** It keeps working; the label
  is only an extra name.
- **The engine's own page or the builder shows everything.** That is
  expected: they ask from inside the container (or with `?all=1`).

# Classifiers (decision models)

llama.cpp's decision models (Julia-1, Laya, Kev, lev, OpenJev, Clef) answer
questions about a text: pick one option, give a score, or yes/no. Argos finds
them by the `<arch>.decision.type` key in the GGUF and lists them under the
kind **Classifier**. They need no special flags; llama-server sets them up
from the file. Make a profile for one like for any model, then call it with
the profile's name in `model`:

```bash
curl http://argos:8080/v1/systemone -H 'Content-Type: application/json' -d '{
  "model": "openjev-router",
  "state": "I was charged twice for my order",
  "questions": {
    "team": {"type": "choice", "instructions": "Which team should handle this?",
             "criteria": {"billing": "payments, refunds, charges", "shipping": "delivery, tracking"}}
  }
}'
```

The answer has `answers.team.choice`, its `probabilities` and `confidence`.
Without `model` the call cannot be routed: Argos hosts many models, so it
always needs one. Option descriptions (as in `criteria` above) answer much
better than bare labels.
