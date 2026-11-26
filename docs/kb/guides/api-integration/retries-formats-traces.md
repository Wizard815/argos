---
title: Retries, API formats and request traces
summary: Try a failed request again and rest a model or provider that keeps failing; serve the Responses and Messages APIs from any model; send each request to an OpenTelemetry or Langfuse collector.
category: guides
tags: [gateway, retry, cooldown, failover, responses-api, messages-api, codex, anthropic, opentelemetry, langfuse, traces, litellm]
updated: 2026-11-25
---

# Retries, API formats and request traces

All three are in Settings -> Gateway and work for every app on Argos's port.

## Retries and cooldown

A local model or cloud provider that fails before answering (500, 502, 503, 504;
a provider's 429 too) is asked again: **Try again** times (default 1),
waiting half a second, or the provider's `Retry-After` up to 5 s.

One that fails **Rest after** times within **…within** seconds rests for
**Rest for** seconds:

- a local model's requests go straight to its cloud fallback (when it has one
  with "when the local model fails"), without waiting for it to fail again;
- a cloud provider answers 503 `provider_cooling_down` at once.

The card lists what rests, with **End now**. Never retried: a request Argos
stopped itself (the loop guard, a cancel on Schedule -> Live, a local 429),
and a model the engine stopped starting after crashes.

## API formats

| app speaks | path | for example |
| --- | --- | --- |
| OpenAI Responses | `/v1/responses` | Codex |
| Anthropic Messages | `/v1/messages` | Claude Code and other Claude apps |

Argos turns such a request into a chat completion and the answer back,
streamed or not, with tool calls, images and reasoning. **Auto** (default)
does it for a cloud provider's model and for a local model whose server
answers 404/405/501 to the path; llama-server answers both itself.
**Always** does it for every model, **never** passes the path on as it is.
The answer carries `X-Argos-Converted`.

Not converted: Responses' built-in tools (web search, file search) and
`previous_response_id` (send the whole conversation, as Codex does).

## Request traces

Each model request becomes one OpenTelemetry span: model, key, app, tokens,
cost, time, status, stream, fallback. Spans go in the background, in batches
(OTLP over HTTP, JSON):

```text
To: OpenTelemetry   Endpoint: http://otel-collector:4318
To: Langfuse        Endpoint: https://cloud.langfuse.com  (+ public and secret key)
```

Grafana Tempo, Jaeger, Phoenix and SigNoz take the OpenTelemetry endpoint.
Prompts and answers are sent only when **Include prompts and answers** is
ticked (cut to 32 KB). **Share of requests** below 1 sends that share.
**Send a test span** says whether the endpoint takes it.

## What goes wrong

- **Every request takes a second longer.** A model keeps failing and each
  request is retried: set Try again to 0, or give it a fallback.
- **Codex says the stream ended early.** The answer hit `max_output_tokens`
  and ended with `response.incomplete`, as OpenAI does: raise the limit.
- **No spans arrive.** The card's counts say failed or dropped, with the last
  error. Spans are dropped while traces are off, and when 2,000 wait unsent.

## Hiding it

Never use this? Settings → General → **Features shown**: untick it and Save. That only hides it in the web page; anything already set keeps working, so turn it off in its own card first if you want it to stop.
