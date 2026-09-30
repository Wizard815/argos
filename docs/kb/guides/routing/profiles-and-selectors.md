---
title: Profiles and selectors
summary: Switch model sets at runtime with profiles, or resolve a virtual model ID per request with selectors.
category: guides
tags: [profiles, selectors, aliases, routing, virtual-models]
config_keys: [profiles, profiles.*.pins, selectors, selectors.*.strategy, selectors.*.targets, hooks.on_startup.profile, profileStateFile]
updated: 2026-09-30
---

# Profiles and selectors

Both let a client ask for a stable model name and get a different real model
behind it. They solve different problems.

## Profiles: switch a whole set at once

A profile is a named set of model ID replacements. One profile, or none, is
active at a time, and you switch it from the web UI or the API.

```yaml
profiles:
  coding:
    description: "Coding-focused model routing"
    pins:
      llm-code: "qwen3-coder-30b"
      llm-plan: "gpt-oss-120b"
      image-gen: ~

  writing:
    description: "Long-form writing"
    pins:
      llm-code: "gemma-3-27b"
      llm-plan: "gemma-3-27b"
```

Your editor always requests `llm-code`. Flip the active profile and it gets a
different model, with no client-side change.

- Pin targets may be a local model, a fully qualified peer model, an alias, a
  `setParamsByID` alias, or a selector.
- An empty string or YAML `~` **disables** the pin — requests get a 404 and it
  is left out of model listings. Existing local, alias and peer IDs stay
  reachable.
- Pins are applied first, before aliases, filters and routing.
- Startup selects the profile remembered in `profileStateFile`, falling back to
  `hooks.on_startup.profile` when there is nothing to remember — see below.
- A pinned model reports the state of its pinned variant, so its card goes green
  while the variant is loaded even though the base model itself never runs.

Switch at runtime:

```console
$ curl http://localhost:8080/api/profiles
$ curl -X PUT http://localhost:8080/api/profiles/active -d '{"name":"coding"}'
$ curl -X PUT http://localhost:8080/api/profiles/active -d '{"name":null}'
```

## Remember the active profile across restarts

The active profile is runtime state, so a restart or a container recreate comes
back with **no** profile active and every client silently falls back to the base
models. Two settings decide what comes back:

- `hooks.on_startup.profile` names the profile to activate on startup.
- `profileStateFile` names a JSON file that remembers the profile **last**
  selected, including "none". A remembered selection wins over the hook; the
  hook is only the fallback for a first run, a deleted file, or a remembered
  name that is no longer configured.

```yaml
profileStateFile: /app/config.d/profile-state.json
hooks:
  on_startup:
    profile: coding
```

The file holds one key and is safe to delete:

```json
{
  "active": "coding"
}
```

Point it at somewhere the proxy can write — a mounted config directory, not the
read-only `config.yaml` mount. Delete the file to fall back to the hook, or
leave `profileStateFile` empty to disable remembering entirely.

## Selectors: resolve per request

A selector is a virtual model ID resolved to a concrete target *on every
request*, based on a strategy.

```yaml
selectors:
  coding-model:
    strategy: warm
    targets: [gpt-oss-120b, qwen3-coder-30b]
    name: "Coding Model"
    description: "Best currently loaded coding model"
```

Three strategies:

**`warm`** — use the first target that is already ready; failing that, one that
is already starting; failing that, cold-start the first. This is the one you
want for "just use whatever is already loaded and don't make me wait".

**`pin`** — always the first target. The remaining entries are reference data
only. Useful as a stable indirection you can edit in one place.

**`spillover`** — fill each target up to a reservation count, then move to the
next. Good for spreading load across local and remote capacity:

```yaml
selectors:
  llama:
    strategy: spillover
    targets: [local-llama, peer-a/llama, peer-b/llama]
    settings:
      spillover: 4     # requests per active target before spilling over
```

Rules worth knowing:

- Selector IDs cannot collide with model IDs, aliases or fully qualified peer
  model names.
- A selector's targets cannot be other selectors.
- Selectors are **not** supported on `/upstream/<model>` paths.
- Profiles run first, so a profile pin may target a selector.

## Which one?

**Profiles** are a mode switch you make deliberately — "I'm coding now". They
change several mappings at once and persist until you change them again.

**Selectors** are automatic, per request, and based on current state. Nobody
flips them.

They compose: pin `llm-code` in the `coding` profile to a `warm` selector, and
you get "when I'm coding, use whichever coding model is already hot".

## Related

- `reference/config/profiles` and `reference/config/selectors`
- `guides/routing/groups-and-matrix` — which models can be loaded at the same time
