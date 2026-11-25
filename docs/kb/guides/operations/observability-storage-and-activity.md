---
title: Observability, storage and Activity
summary: Use logs, metrics, captures and the Activity view to diagnose requests and retain useful history.
category: guides
tags: [operations, logs, metrics, activity, captures]
config_keys: [logLevel, logToStdout, metricsMaxInMemory, captureBuffer, store.path, store.captures.enabled, store.captures.maxRows]
updated: 2026-11-25
---

# Observability, storage and Activity

Use the Activity view for recent request timing and model events, logs for
process and proxy failures, and metrics for trends. `metricsMaxInMemory` and
`captureBuffer` bound retained in-memory data; increase them only when the
memory cost is acceptable.

```yaml
logLevel: debug
logToStdout: true
metricsMaxInMemory: 1000
captureBuffer: 100
```

Do not put secrets in captures or debug logs. Reduce retention after diagnosing
an issue.

## Keeping captures across restarts

`captureBuffer` captures live in memory and are gone after a restart. Set
`store.captures.enabled` to also write each capture to the store's `captures`
table. Its `id` is the activity row's id, so the two tables join. This works
with `captureBuffer: 0` too; the store is then the only copy.

```yaml
store:
  path: /data/argos.db   # or a postgres:// URL
  captures:
    enabled: true
    maxRows: 10000       # newest kept; -1 keeps all
```

Each row keeps the redacted headers, the request and response bodies as text,
and two readable columns: `prompt_text` (the last user message) and
`response_text` (the model's answer, with streamed chunks joined and tool
calls written as `[tool] name(arguments)`). Tokens, duration, source and
client come from the matching activity row.

Search and page with `GET /api/captures`:

- `q`: text in the prompt, the answer or the request body (any case)
- `model`, `path`, `status`, `src` (source prefix)
- `since`, `until`: RFC 3339 or Unix seconds
- `view=summary`: leave out headers and raw bodies
- `limit` (1 to 1000, default 100), `order=asc|desc`

A collector reads oldest first and passes the returned `next_after_id` as
`after_id`. An empty `data` means it is caught up. With `order=desc`, pass
`next_before_id` as `before_id` instead. Example:
`/api/captures?q=kitchen&model=qwen&order=desc&view=summary`. The endpoint
answers 404 when `store.captures.enabled` is off.

Argos redacts `Authorization`, cookies, `X-Api-Key` and any header named like a
token, secret, password or API key before writing. It does not touch bodies:
prompts and answers are stored as sent. With no `store.path` the table is in
memory and is lost on restart like everything else. Without `maxRows` the
table keeps 10000 captures. A chat with a long system prompt and tool list can
be 50 KB, so 10000 of those is about 500 MB; watch disk use, or let a collector
copy rows out and keep the table small.
