---
title: Routing capacity and request queues
summary: Configure concurrencyLimit and globalConcurrencyLimit, and understand queued work while a model is loading or busy.
category: guides
tags: [routing, queue, capacity, concurrency, concurrency-limit, max-concurrent-requests, global-concurrency-limit, rate-limit]
config_keys: [routing, models.*.concurrencyLimit, models.*.concurrencyQueue, globalConcurrencyLimit]
updated: 2026-11-25
---

# Routing capacity and request queues

There is no `models.*.maxConcurrentRequests` setting. The real per-model key
is `models.*.concurrencyLimit`, which limits active parallel requests.

## Over the limit: wait in line

A model answers `concurrencyLimit` requests at once (10 when unset). The next
ones wait in a line for that model and are served oldest first as running
requests finish, so a burst from an agent is slowed down, not failed. The
line holds `concurrencyQueue` requests (200 when unset); one past it gets an
HTTP 429. Schedule -> Live lists the waiting requests.

```yaml
models:
  qwen-27b:
    cmd: llama-server --port ${PORT} -m /models/qwen-27b.gguf --parallel 4
    concurrencyLimit: 4    # what --parallel can really run at once
    concurrencyQueue: 50   # the rest wait; the 55th at once gets a 429
```

`concurrencyQueue: -1` brings back the old behaviour: every request over the
limit gets a 429 at once. A model with requests waiting in line is busy, so
the pool does not unload it for another model until the line is empty.

The router serializes model swaps and queues requests until the selected model
is ready. Avoid using many simultaneous client retries as a capacity control:
they create more queued work. Choose a routing policy that fits the models that
may coexist, then set client timeouts high enough for the queue and load time.

Inspect the Activity view and logs when latency grows. A queue that never drains
usually means the command, proxy, or health check is wrong; see
`guides/model-runtime/troubleshooting-model-wont-load`.

## Global concurrency limit

`globalConcurrencyLimit` is a top-level setting, separate from
`models.*.concurrencyLimit`. It caps the number of inference requests served
at once across every model combined, using a single shared semaphore:

```yaml
globalConcurrencyLimit: 8
```

The default is `0`, meaning no limit — the semaphore is not even added to the
request chain, so a default config pays no cost for the feature. Once the
limit is reached, further requests are rejected immediately with an HTTP 429
response rather than queued; there is no wait for a slot to free up. Clients
should treat a 429 as a signal to back off and retry, the same way they handle
a per-model concurrency limit rejection.

Use this to protect shared hardware (CPU, disk, network) from being
overwhelmed by traffic spread across many different models, which a per-model
`concurrencyLimit` cannot do since it only counts requests to one model at a
time.
