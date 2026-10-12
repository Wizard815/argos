---
title: API keys, access control, and keeping secrets out of your config
summary: apiKeys authentication has no per-key rate limits or user permissions; use env macros for secrets.
category: guides
tags: [security, api-keys, auth, secrets, env, rate-limit, users, permissions, access-control]
config_keys: [apiKeys, macros, models.*.env]
updated: 2026-08-25
---

# API keys and keeping secrets out of your config

## Requiring a key

By default Argos is unauthenticated. Add `apiKeys` and every request needs
one:

```yaml
apiKeys:
  - "sk-hunter2"
```

Clients may present it as `Authorization: Bearer <key>`, `x-api-key: <key>`, or
HTTP Basic. The web UI and everything under `/api/` are covered too.

Generate a real one:

```console
$ printf "sk-%s\n" "$(head -c 48 /dev/urandom | base64)"
```

Multiple keys are allowed, which is how you rotate without downtime: add the
new key, move clients over, remove the old one.

All keys are equivalent. Argos has no per-key rate limiting, user
accounts, roles, or per-key permissions. Put a reverse proxy or API gateway in
front of Argos when you need those controls.

**`apiKeys` is not a substitute for a firewall.** Argos starts processes
on your machine. Do not expose it to the internet on the strength of a bearer
token alone.

## Keep the keys out of the file

Use env macros so the config itself is safe to commit:

```yaml
apiKeys:
  - "${env.ARGOS_KEY}"
  - "${env.ARGOS_KEY_ROTATE}"
```

`${env.VAR}` is substituted before anything else. **If the variable is not set,
config loading fails with an error** — which is what you want. A typo becomes a
startup failure rather than an instance running with an empty key list.

The same applies anywhere a secret appears:

```yaml
models:
  hf-model:
    env:
      - "HF_TOKEN=${env.HF_TOKEN}"
    cmd: llama-server --port ${PORT} -hf some/repo
```

## What ends up in your config file

Worth being aware of, because config files get pasted into issues and chats:

- `apiKeys` — literal keys, unless you used env macros
- `models.*.env` — literal `NAME=value` pairs
- `models.*.cmd` / `cmdStop` — full command lines, which often carry
  `--api-key` flags and absolute paths
- `macros` — **after** env substitution has already run

That last one is the non-obvious one: a macro like
`llama: "llama-server --api-key ${env.KEY}"` holds the resolved secret once the
config is loaded.

Before sharing a config, strip those. Redact rather than delete so the shape of
the problem survives.

## Related

- `reference/config/apiKeys`
- `guides/configuration/macros` — how env macros resolve
