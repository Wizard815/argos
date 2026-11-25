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

A collector reads new captures with `GET /api/captures?after_id=N&limit=M`
(oldest first, up to 1000 a page) and passes the returned `next_after_id` on
the next call. An empty `data` means it is caught up. The endpoint answers 404
when `store.captures.enabled` is off. Bodies come back base64-encoded.

Argos redacts `Authorization`, cookies, `X-Api-Key` and any header named like a
token, secret, password or API key before writing. It does not touch bodies:
prompts and answers are stored as sent. With no `store.path` the table is in
memory and is lost on restart like everything else. Without `maxRows` the
table keeps 10000 captures; large bodies make that grow, so watch disk use.
