---
title: Proxy and model startup timeouts
summary: Health-check and startup timeouts, slow big models, and the occasional 502 after a streamed answer.
category: guides
tags: [proxy, timeout, health-check, loading]
config_keys: [healthCheckTimeout, models.*.proxy, models.*.checkEndpoint, disableKeepAlives, models.*.disableKeepAlives]
updated: 2026-11-20
---

# Proxy and model startup timeouts

`healthCheckTimeout` limits how long Argos waits for a newly started model
to become healthy. `proxy` must point to the model server that Argos can
reach, and `checkEndpoint` must return success there.

If a request times out after the process starts, verify the proxy URL and
endpoint from the Argos host first. Raising a timeout only hides a wrong
port, Docker mapping, or unavailable health endpoint.

## A big model that loads for longer than the timeout

While a model loads, llama-server answers its health check with 503
`{"error":{"message":"Loading model"}}`. When the answer says it is getting
on (a higher `progress`, or a new message), Argos starts the
`healthCheckTimeout` countdown again. A server that keeps giving the same
answer still runs out of it, so a stuck load is not waited on forever.
A llama-server that reports no progress gets the plain timeout: set it to
your slowest model's load time.

## 502 "proxy error: EOF" now and then

Argos reuses its connection to a model between requests. llama-server can
close one after a streamed answer, and the next request sent on it fails
with 502 `proxy error: EOF`. Turn reuse off, for every model or for one:

```yaml
disableKeepAlives: true       # every model
models:
  qwen3-coder:
    cmd: llama-server --port ${PORT} -m /app/models/qwen3-coder.gguf
    disableKeepAlives: false  # this one keeps reusing
```

Each request then opens a new local connection, which costs well under a
millisecond.

## Errors that showed as 200

A client that sends a large body (over about 1 MiB) with
`Expect: 100-continue` (curl does) first gets `100 Continue`. Argos passes it
on and then the model's real answer, so a 400 "context too long" arrives as
400, not as an empty 200.
