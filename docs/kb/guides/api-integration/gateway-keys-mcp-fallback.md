---
title: Keys, MCP gateway and cloud fallback
summary: Per-key models, MCP servers and limits, one MCP link for every server or one each, MCP tools run inside a chat request, an answer cache and a cloud fallback.
category: guides
tags: [api-keys, litellm, gateway, mcp, rate-limit, quota, usage, fallback, cache, model-groups]
config_keys: [apiKeys]
updated: 2026-11-25
---

# Keys, MCP gateway and cloud fallback

What LiteLLM did in front of Argos, Argos now does on its own port.

## Keys (Settings -> Security -> Edit)

| setting | what it does |
| --- | --- |
| models | names as apps see them, `*` patterns, or `@group` (Settings -> Gateway). Blank = every model. The app's `/v1/models` shows only these; another model answers 403 `model_not_allowed`. |
| MCP servers | every one, only some, every one except some, or none |
| requests a minute, tokens a minute, requests at once | answered 429 `rate_limit_exceeded` when over |
| tokens a day, tokens a month | a quota, from the usage below |
| expires on, blocked | refused with 401 and the reason |

**Usage per key** (same tab) counts requests and tokens per key, day and
model, streamed or not. Limits are kept in memory; quotas in the database.

## MCP gateway (Settings -> MCP)

Add each MCP server once: a command Argos starts (stdio; the program must be
in the Argos container) or a URL (streamable HTTP). Headers and environment
can say `${NAME}` to read a token from Argos's `.env`. Turn tools off per
server; small models do better with fewer.

- **All in one:** `http://argos:8080/mcp`, tools named `<server>__<tool>`.
  `?skip=github` or `?only=files,web` narrows it for one app.
- **Single:** `http://argos:8080/mcp/<server>`, tools under their own names.

An app sends its Argos key; a server the key may not use is left out of
`/mcp` and its own link answers 403.

### MCP tools inside a chat request

```json
{"model": "qwen3.8-27b-uncen-mtp-2chat-512k",
 "messages": [{"role": "user", "content": "What changed in the repo today?"}],
 "tools": [{"type": "mcp", "server_url": "argos/github", "require_approval": "never"}]}
```

Argos gives the model the server's tools, runs the ones it calls and asks
again, up to `max_tool_rounds` (5, at most 10). The answer has `argos_mcp`
with each call. Without `require_approval: "never"` nothing runs: the tool
calls go back to the app. A streamed request gets the answer in one chunk.

## Cloud providers (Settings -> Providers)

Add an OpenAI-compatible provider (OpenRouter: `https://openrouter.ai/api/v1`)
with its key, then **Pick models** from its list (prices, context and tool
support shown). Each is served as `<provider>/<model>`, for example
`openrouter/tencent/hy4-flash`, and goes to the provider under its own name
with the provider's key. Key rules, limits, usage, the cache and MCP tools in
chat apply as for local models; a key's model list can say `openrouter/*`.
They use no VRAM and come last in the model list.

## Answer cache (Settings -> Gateway)

The exact same request again is answered from memory, with
`X-Argos-Cache: hit`. Only temperature 0 requests and embeddings, never
streamed ones.

## Cloud fallback (Settings -> Gateway)

For models matching a name or pattern, send the request to another
OpenAI-compatible server when the local model fails (5xx), the prompt does
not fit its context, or it has not started answering after N seconds. The
answer carries `X-Argos-Fallback` and the Logs page says why.

## What goes wrong

- **A streamed request is still slow to fall back.** The wait counts until
  the engine starts answering; a stream starts early while a model loads.
- **A stdio MCP server says "could not start".** Its program (npx, uvx) is not
  in the Argos container; run it as its own container and add it by URL.
- **LiteLLM in front still lists old models.** It caches the list; restart it,
  or point apps at Argos directly.
