---
title: Keys, MCP gateway and cloud fallback
summary: Per-key and per-app models, MCP servers and tools, limits and dollar budgets, one MCP link for every server or one each, MCP tools run inside a chat request, an answer cache and a cloud fallback.
category: guides
tags: [api-keys, litellm, gateway, apps, loop-guard, budget, cost, mcp, rate-limit, quota, usage, fallback, cache, model-groups]
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
| dollars a day, dollars a month | a budget for cloud models (providers, cloud fallbacks); once spent they answer 429 `budget_exceeded` and local models still answer |
| only these / never these MCP tools | `<server>__<tool>` patterns: a tool it may not use is not listed and its call is refused |
| expires on, blocked | refused with 401 and the reason |

**Usage per key** (same tab) counts requests, tokens and cloud cost per key,
day and model, streamed or not. Limits are kept in memory; quotas and
budgets in the database.

A request's cost is its tokens at the provider's price: from the provider's
model list (OpenRouter's says), else the prices set on the provider or the
fallback (dollars per million tokens in, out). A model with no known price
costs $0 and is never refused by a budget. The budget is checked before each
request, so requests already running when it runs out can overshoot it a
little.

## One key for a group of apps (LiteLLM in front)

A key can stand for several apps, named for the group ("litellm",
"coding"). **Apps through each key** (Settings -> Security) lists the apps
seen through it, with their requests and tokens. The loop guard stops only
the app that loops, and each app takes its turn in a busy model's line.

An app is known by, first found: an `X-Argos-App` header; its LiteLLM key's
alias (or team alias); an `X-Title` header; its User-Agent without the
version. Behind LiteLLM the User-Agent is LiteLLM's own, the same for every
app, so give each app its own LiteLLM key with an alias and let LiteLLM send
it:

```yaml
# LiteLLM config.yaml
general_settings:
  forward_client_headers_to_llm_api: true   # forwards the apps' x- headers (X-Title, X-Argos-App)
litellm_settings:
  add_user_information_to_llm_headers: true # adds x-litellm-user_api_key_alias and team alias
```

**Rules for one app** (Apps through each key -> Rules): models, the same
limits, tokens and dollars a day or month, MCP tools it may never use, or
blocked. They apply on top of the key's, to that app only: the key's other
apps go on. An app with rules is listed even before it first comes.

The second setting also sends the LiteLLM user's email and IDs to Argos.
Argos reads only the key and team aliases from them. A name is what an app
says it is: it tells honest apps apart, it does not authenticate them.

## MCP gateway (Settings -> MCP)

**Tool calls** (same tab) lists the latest calls through `/mcp` and MCP tools
run in chat: key, app, tool, result, time and arguments (cut to 500
characters).

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
- **A budget never stops a cloud model.** Its price is unknown: set it on the
  provider (Settings -> Providers -> Edit -> Prices) or the fallback.
- **Every app behind LiteLLM shows as "OpenAI/Python".** LiteLLM is not
  sending the key alias: set both settings above, and give each app a LiteLLM
  key with an alias.
- **LiteLLM in front still lists old models.** It caches the list; restart it,
  or point apps at Argos directly.

## Turning it off or hiding it

Settings → General → **Features** has two switches per feature:

- **Working** off: Argos skips the feature on every request, whatever its own settings say. They are kept, so ticking it again brings them back as they were. Its card shows an "off: not used" pill.
- **Shown** off: it is only hidden on the web page. If Working is still ticked it keeps working.
