"""Speak the Responses API and the Anthropic Messages API to backends that only know Chat Completions.

Codex CLI and other new OpenAI apps send ``/v1/responses``; Claude-style apps send
``/v1/messages``. Many backends (cloud providers like OpenRouter, local servers)
only answer ``/v1/chat/completions``. These functions turn such a request into a
chat request, and turn the chat answer back: a plain JSON answer with
``chat_to_response`` / ``chat_to_message``, an SSE stream with
``ChatToResponsesStream`` / ``ChatToMessagesStream``.

Everything here is pure (no Flask, no network) so the proxy can wire it in where
it likes. Input from a backend is never trusted to be well formed: missing fields
get defaults and lines that are not JSON are skipped, so a strange backend gives
a strange answer, not a crash in the middle of a stream.
"""
from __future__ import annotations

import json
import time
import uuid
from typing import Any, Optional

# ---------------------------------------------------------------- small helpers


def _hex() -> str:
    return uuid.uuid4().hex


def _dict(v: Any) -> dict:
    return v if isinstance(v, dict) else {}


def _list(v: Any) -> list:
    return v if isinstance(v, list) else []


def _int(v: Any) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def _str(v: Any) -> str:
    """Text for a field that should be a string; JSON for anything else."""
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    try:
        return json.dumps(v)
    except (TypeError, ValueError):
        return str(v)


def _parts_text(content: Any, sep: str = "\n") -> str:
    """The text of a content field: a string, or a list of parts that carry ``text``."""
    if isinstance(content, str):
        return content
    texts = []
    for p in _list(content):
        if isinstance(p, str):
            texts.append(p)
        elif isinstance(p, dict) and isinstance(p.get("text"), str):
            texts.append(p["text"])
    return sep.join(texts)


def _chat_choice(chat: Any) -> tuple[dict, Optional[str]]:
    """The first choice's message and finish reason of a chat answer."""
    choices = _list(_dict(chat).get("choices"))
    first = _dict(choices[0]) if choices else {}
    return _dict(first.get("message")), first.get("finish_reason")


def _reasoning_of(msg: dict) -> str:
    # llama-server and vLLM say reasoning_content; OpenRouter says reasoning.
    for key in ("reasoning_content", "reasoning"):
        if isinstance(msg.get(key), str) and msg[key]:
            return msg[key]
    return ""


def _with_usage(out: dict) -> dict:
    # Without include_usage a streamed chat answer has no token counts at all.
    if out.get("stream"):
        out["stream_options"] = {"include_usage": True}
    return out


# ------------------------------------------------------------ Responses -> chat


def _responses_part(p: Any) -> Optional[dict]:
    """One Responses content part as a chat content part (None to drop it)."""
    if isinstance(p, str):
        return {"type": "text", "text": p}
    p = _dict(p)
    kind = p.get("type")
    if kind in ("input_text", "output_text", "text", "refusal"):
        text = p.get("text") if kind != "refusal" else p.get("refusal")
        return {"type": "text", "text": _str(text)}
    if kind == "input_image":
        url = p.get("image_url")
        if isinstance(url, dict):
            url = url.get("url")
        if not isinstance(url, str) or not url:
            return None  # a file_id needs the OpenAI file store; a chat backend has none
        image = {"url": url}
        if p.get("detail") in ("low", "high", "auto"):
            image["detail"] = p["detail"]
        return {"type": "image_url", "image_url": image}
    return None


def _chat_content(parts: list[dict]) -> Any:
    """Plain text stays a string: many local servers only accept strings for system and assistant turns."""
    if all(p["type"] == "text" for p in parts):
        return "\n".join(p["text"] for p in parts)
    return parts


def _output_text(output: Any) -> str:
    """The text of a function_call_output's output (a string, parts, or anything JSON)."""
    if isinstance(output, str):
        return output
    if isinstance(output, list):
        return _parts_text(output)
    return _str(output)


def responses_to_chat(body: dict) -> dict:
    """A Responses API request as a Chat Completions request."""
    body = _dict(body)
    messages: list[dict] = []
    if isinstance(body.get("instructions"), str) and body["instructions"]:
        messages.append({"role": "system", "content": body["instructions"]})

    items = body.get("input")
    if isinstance(items, str):
        items = [{"type": "message", "role": "user", "content": items}]
    for item in _list(items):
        item = _dict(item)
        kind = item.get("type") or ("message" if "role" in item else None)
        if kind == "message":
            role = item.get("role") or "user"
            if role == "developer":
                role = "system"
            content = item.get("content")
            if isinstance(content, str):
                messages.append({"role": role, "content": content})
                continue
            parts = [q for q in (_responses_part(p) for p in _list(content)) if q]
            messages.append({"role": role, "content": _chat_content(parts)})
        elif kind in ("function_call", "custom_tool_call"):
            args = item.get("arguments") if kind == "function_call" else item.get("input")
            call = {"id": _str(item.get("call_id") or item.get("id") or "call_" + _hex()),
                    "type": "function",
                    "function": {"name": _str(item.get("name")), "arguments": _str(args)}}
            last = messages[-1] if messages else None
            # One assistant turn: its text and the calls that follow it, or a run of calls.
            if last is not None and last["role"] == "assistant":
                last.setdefault("tool_calls", []).append(call)
                if last.get("content") == "":
                    last["content"] = None
            else:
                messages.append({"role": "assistant", "content": None, "tool_calls": [call]})
        elif kind in ("function_call_output", "custom_tool_call_output"):
            messages.append({"role": "tool", "tool_call_id": _str(item.get("call_id")),
                             "content": _output_text(item.get("output"))})
        # reasoning and other item types have no chat form: dropped.

    out: dict[str, Any] = {"model": body.get("model"), "messages": messages}

    tools = []
    for t in _list(body.get("tools")):
        t = _dict(t)
        if t.get("type") != "function" or not t.get("name"):
            continue  # web_search, file_search, custom... are run by OpenAI, not by a chat backend
        fn = {"name": t["name"], "parameters": t.get("parameters") or {"type": "object", "properties": {}}}
        if t.get("description"):
            fn["description"] = t["description"]
        tools.append({"type": "function", "function": fn})
    if tools:
        out["tools"] = tools
        choice = body.get("tool_choice")
        if choice in ("auto", "none", "required"):
            out["tool_choice"] = choice
        elif isinstance(choice, dict) and choice.get("type") == "function" and choice.get("name"):
            out["tool_choice"] = {"type": "function", "function": {"name": choice["name"]}}
    elif body.get("tool_choice") == "none":
        out["tool_choice"] = "none"

    if body.get("max_output_tokens") is not None:
        out["max_tokens"] = body["max_output_tokens"]
    for key in ("temperature", "top_p", "parallel_tool_calls", "stop", "user", "seed"):
        if body.get(key) is not None:
            out[key] = body[key]
    if "parallel_tool_calls" in out and not tools:
        del out["parallel_tool_calls"]  # OpenAI rejects it without tools
    if body.get("stream"):
        out["stream"] = True

    fmt = _dict(_dict(body.get("text")).get("format"))
    if fmt.get("type") == "json_schema":
        schema = {"name": fmt.get("name") or "response", "schema": fmt.get("schema") or {}}
        if fmt.get("strict") is not None:
            schema["strict"] = fmt["strict"]
        if fmt.get("description"):
            schema["description"] = fmt["description"]
        out["response_format"] = {"type": "json_schema", "json_schema": schema}
    elif fmt.get("type") == "json_object":
        out["response_format"] = {"type": "json_object"}
    return _with_usage(out)


# ------------------------------------------------------------ chat -> Responses


def _responses_usage(usage: Any) -> dict:
    usage = _dict(usage)
    inp, outp = _int(usage.get("prompt_tokens")), _int(usage.get("completion_tokens"))
    return {"input_tokens": inp,
            "input_tokens_details": {"cached_tokens": _int(_dict(usage.get("prompt_tokens_details")).get("cached_tokens"))},
            "output_tokens": outp,
            "output_tokens_details": {"reasoning_tokens": _int(_dict(usage.get("completion_tokens_details")).get("reasoning_tokens"))},
            "total_tokens": _int(usage.get("total_tokens")) or inp + outp}


def _response_obj(request: dict, rid: str, created: int, model: Any, status: str, output: list,
                  usage: Optional[dict]) -> dict:
    """A Responses object, echoing the request's settings the way OpenAI does."""
    request = _dict(request)
    text = "".join(c.get("text", "") for item in output if item.get("type") == "message"
                   for c in item.get("content", []) if c.get("type") == "output_text")
    obj = {
        "id": rid, "object": "response", "created_at": created, "status": status,
        "error": None, "incomplete_details": None,
        "instructions": request.get("instructions"),
        "max_output_tokens": request.get("max_output_tokens"),
        "model": model or request.get("model") or "",
        "output": output, "output_text": text,
        "parallel_tool_calls": request.get("parallel_tool_calls", True),
        "previous_response_id": request.get("previous_response_id"),
        "reasoning": request.get("reasoning") or {"effort": None, "summary": None},
        "store": request.get("store", False),
        "temperature": request.get("temperature", 1.0),
        "text": request.get("text") or {"format": {"type": "text"}},
        "tool_choice": request.get("tool_choice", "auto"),
        "tools": request.get("tools") or [],
        "top_p": request.get("top_p", 1.0),
        "truncation": request.get("truncation", "disabled"),
        "usage": usage,
        "user": request.get("user"),
        "metadata": request.get("metadata") or {},
    }
    if status == "incomplete":
        obj["incomplete_details"] = {"reason": "max_output_tokens"}
    return obj


def _reasoning_item(iid: str, text: str) -> dict:
    return {"type": "reasoning", "id": iid, "summary": [],
            "content": [{"type": "reasoning_text", "text": text}]}


def _message_item(iid: str, text: str, status: str) -> dict:
    return {"type": "message", "id": iid, "status": status, "role": "assistant",
            "content": [{"type": "output_text", "text": text, "annotations": []}]}


def _call_item(iid: str, call_id: str, name: str, args: str, status: str) -> dict:
    return {"type": "function_call", "id": iid, "call_id": call_id, "name": name,
            "arguments": args, "status": status}


def chat_to_response(chat: dict, request: dict) -> dict:
    """A non-stream chat answer as a Responses object."""
    msg, finish = _chat_choice(chat)
    output = []
    reasoning = _reasoning_of(msg)
    if reasoning:
        output.append(_reasoning_item("rs_" + _hex(), reasoning))
    text = _parts_text(msg.get("content"), "")
    if text:
        output.append(_message_item("msg_" + _hex(), text, "completed"))
    for tc in _list(msg.get("tool_calls")):
        fn = _dict(_dict(tc).get("function"))
        output.append(_call_item("fc_" + _hex(), _str(tc.get("id")) or "call_" + _hex(),
                                 _str(fn.get("name")), _str(fn.get("arguments")), "completed"))
    status = "incomplete" if finish == "length" else "completed"
    created = _int(_dict(chat).get("created")) or int(time.time())
    return _response_obj(request, "resp_" + _hex(), created, _dict(request).get("model") or _dict(chat).get("model"),
                         status, output, _responses_usage(_dict(chat).get("usage")))


# ------------------------------------------------------------ Messages -> chat


def _anthropic_image(block: dict) -> Optional[dict]:
    src = _dict(block.get("source"))
    if src.get("type") == "base64" and src.get("data"):
        url = f"data:{src.get('media_type') or 'image/png'};base64,{src['data']}"
    elif src.get("type") == "url" and src.get("url"):
        url = src["url"]
    else:
        return None
    return {"type": "image_url", "image_url": {"url": url}}


def messages_to_chat(body: dict) -> dict:
    """An Anthropic Messages request as a Chat Completions request."""
    body = _dict(body)
    messages: list[dict] = []
    system = _parts_text(body.get("system"))
    if system:
        messages.append({"role": "system", "content": system})

    for m in _list(body.get("messages")):
        m = _dict(m)
        role = m.get("role") if m.get("role") in ("user", "assistant") else "user"
        content = m.get("content")
        if isinstance(content, str):
            messages.append({"role": role, "content": content})
            continue
        parts: list[dict] = []
        calls: list[dict] = []
        for block in _list(content):
            block = _dict(block)
            kind = block.get("type")
            if kind == "text":
                parts.append({"type": "text", "text": _str(block.get("text"))})
            elif kind == "image":
                image = _anthropic_image(block)
                if image:
                    parts.append(image)
            elif kind == "tool_use" and role == "assistant":
                calls.append({"id": _str(block.get("id")) or "call_" + _hex(), "type": "function",
                              "function": {"name": _str(block.get("name")),
                                           "arguments": json.dumps(block.get("input") if block.get("input") is not None else {})}})
            elif kind == "tool_result" and role == "user":
                # A chat backend wants tool answers right after the assistant's calls, so they go first.
                messages.append({"role": "tool", "tool_call_id": _str(block.get("tool_use_id")),
                                 "content": _parts_text(block.get("content"))})
            # thinking, redacted_thinking and server tool blocks have no chat form: dropped.
        if role == "assistant":
            if parts or calls:
                msg: dict[str, Any] = {"role": "assistant", "content": _parts_text(parts) if parts else None}
                if calls:
                    msg["tool_calls"] = calls
                messages.append(msg)
        elif parts:
            messages.append({"role": "user", "content": _chat_content(parts)})

    out: dict[str, Any] = {"model": body.get("model"), "messages": messages}
    tools = []
    for t in _list(body.get("tools")):
        t = _dict(t)
        if not t.get("name") or t.get("type") not in (None, "custom") or "input_schema" not in t:
            continue  # server tools (web_search_..., bash_..., text_editor_...) are run by Anthropic
        fn = {"name": t["name"], "parameters": t.get("input_schema") or {"type": "object", "properties": {}}}
        if t.get("description"):
            fn["description"] = t["description"]
        tools.append({"type": "function", "function": fn})
    choice = _dict(body.get("tool_choice"))
    if tools:
        out["tools"] = tools
        kind = choice.get("type")
        if kind == "auto":
            out["tool_choice"] = "auto"
        elif kind == "any":
            out["tool_choice"] = "required"
        elif kind == "tool" and choice.get("name"):
            out["tool_choice"] = {"type": "function", "function": {"name": choice["name"]}}
        elif kind == "none":
            out["tool_choice"] = "none"
        if choice.get("disable_parallel_tool_use"):
            out["parallel_tool_calls"] = False

    for key in ("max_tokens", "temperature", "top_p"):
        if body.get(key) is not None:
            out[key] = body[key]
    if body.get("stop_sequences"):
        out["stop"] = body["stop_sequences"]
    user = _dict(body.get("metadata")).get("user_id")
    if user:
        out["user"] = _str(user)
    if body.get("stream"):
        out["stream"] = True
    return _with_usage(out)


# ------------------------------------------------------------- chat -> Messages


def _stop_reason(finish: Any, has_calls: bool) -> str:
    if finish == "length":
        return "max_tokens"
    if finish in ("tool_calls", "function_call") or has_calls:
        return "tool_use"
    # Chat answers do not say which stop sequence matched, so a plain stop is end_turn.
    return "end_turn"


def _parse_args(args: Any) -> dict:
    if isinstance(args, dict):
        return args
    try:
        v = json.loads(args or "{}")
    except (TypeError, ValueError):
        return {}
    return v if isinstance(v, dict) else {}


def chat_to_message(chat: dict, request: dict) -> dict:
    """A non-stream chat answer as an Anthropic message."""
    msg, finish = _chat_choice(chat)
    content: list[dict] = []
    reasoning = _reasoning_of(msg)
    if reasoning:
        content.append({"type": "thinking", "thinking": reasoning, "signature": ""})
    text = _parts_text(msg.get("content"), "")
    if text:
        content.append({"type": "text", "text": text})
    calls = _list(msg.get("tool_calls"))
    for tc in calls:
        fn = _dict(_dict(tc).get("function"))
        content.append({"type": "tool_use", "id": _str(tc.get("id")) or "toolu_" + _hex(),
                        "name": _str(fn.get("name")), "input": _parse_args(fn.get("arguments"))})
    usage = _dict(_dict(chat).get("usage"))
    return {"id": "msg_" + _hex(), "type": "message", "role": "assistant",
            "model": _dict(request).get("model") or _dict(chat).get("model") or "",
            "content": content, "stop_reason": _stop_reason(finish, bool(calls)), "stop_sequence": None,
            "usage": {"input_tokens": _int(usage.get("prompt_tokens")),
                      "output_tokens": _int(usage.get("completion_tokens"))}}


# ---------------------------------------------------------------------- errors


def _error_fields(status: int, body: bytes) -> tuple[str, Any, Any]:
    """Message, type and code of an upstream error body, whatever shape it has."""
    try:
        data = json.loads(body or b"")
    except (TypeError, ValueError):
        data = None
    if data is None:
        text = body.decode("utf-8", "replace").strip() if isinstance(body, (bytes, bytearray)) else _str(body)
        return text or f"upstream error {status}", None, None
    data = _dict(data) if not isinstance(data, str) else {"message": data}
    err = data.get("error", data)
    if isinstance(err, str):
        return err, None, None
    err = _dict(err)
    message = err.get("message") or err.get("detail") or data.get("message") or data.get("detail")
    return _str(message) or f"upstream error {status}", err.get("type"), err.get("code")


def _openai_type(status: int) -> str:
    return {400: "invalid_request_error", 401: "authentication_error", 403: "permission_error",
            404: "not_found_error", 429: "rate_limit_error"}.get(status, "server_error" if status >= 500 else "invalid_request_error")


def _anthropic_type(status: int) -> str:
    return {400: "invalid_request_error", 401: "authentication_error", 403: "permission_error",
            404: "not_found_error", 413: "request_too_large", 429: "rate_limit_error",
            503: "overloaded_error", 529: "overloaded_error"}.get(status, "api_error" if status >= 500 else "invalid_request_error")


def error_to_responses(status: int, body: bytes) -> dict:
    """An upstream chat error as a Responses API error body."""
    message, kind, code = _error_fields(status, body)
    return {"error": {"message": message, "type": kind if isinstance(kind, str) and kind else _openai_type(status),
                      "code": code}}


def error_to_messages(status: int, body: bytes) -> dict:
    """An upstream chat error as an Anthropic error body (its type is always one Anthropic defines)."""
    message, _, _ = _error_fields(status, body)
    return {"type": "error", "error": {"type": _anthropic_type(status), "message": message}}


# ---------------------------------------------------------------- chat streams

_DONE = object()


class _ChatSSE:
    """Split a chat SSE byte stream into JSON chunks, whatever the network chunking."""

    def __init__(self) -> None:
        self._buf = b""
        self._seen_data = False
        self._raw = b""  # what came without "data:", in case the backend ignored stream and sent JSON

    def feed(self, chunk: bytes) -> list:
        # Bytes are split on newlines before decoding so a UTF-8 character cut in two is not lost.
        self._buf += chunk or b""
        out: list = []
        while True:
            i = self._buf.find(b"\n")
            if i < 0:
                return out
            line, self._buf = self._buf[:i], self._buf[i + 1:]
            out.extend(self._line(line))

    def flush(self) -> list:
        line, self._buf = self._buf, b""
        out = self._line(line) if line else []
        if not self._seen_data and self._raw.strip():
            out.extend(self._whole_answer())
        return out

    def _line(self, line: bytes) -> list:
        line = line.strip()
        if not line.startswith(b"data:"):
            if not self._seen_data and len(self._raw) < 8 << 20:
                self._raw += line + b"\n"
            return []
        self._seen_data = True
        payload = line[5:].strip()
        if payload == b"[DONE]":
            return [_DONE]
        try:
            obj = json.loads(payload)
        except ValueError:
            return []
        return [obj] if isinstance(obj, dict) else []

    def _whole_answer(self) -> list:
        """A non-stream chat answer, or error, replayed as stream chunks."""
        try:
            obj = json.loads(self._raw)
        except ValueError:
            return []
        if not isinstance(obj, dict):
            return []
        if "choices" not in obj:
            return [obj] if obj.get("error") else []
        msg, finish = _chat_choice(obj)
        delta = dict(msg)
        delta["tool_calls"] = [dict(_dict(tc), index=i) for i, tc in enumerate(_list(msg.get("tool_calls")))]
        return [{"model": obj.get("model"), "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
                 "usage": obj.get("usage")}, _DONE]


class _ChatStream:
    """Read chat stream chunks and call a hook per piece: reasoning, text, tool call, end, error."""

    def __init__(self, request: dict) -> None:
        self.request = _dict(request)
        self.model = self.request.get("model")
        self.usage: Optional[dict] = None
        self.finish_reason: Optional[str] = None
        self._sse = _ChatSSE()
        self._started = False
        self._ended = False

    def feed(self, chunk: bytes) -> bytes:
        """SSE bytes to send for this piece of the backend's stream."""
        if self._ended:
            return b""
        return self._run(self._sse.feed(chunk))

    def close(self) -> bytes:
        """The last SSE bytes: whatever was buffered, and the closing events if the backend never sent [DONE]."""
        if self._ended:
            return b""
        out = self._run(self._sse.flush())
        if not self._ended:
            self._ended = True
            out += b"".join(self._end())
        return out

    def _run(self, chunks: list) -> bytes:
        out: list[bytes] = []
        if not self._started:
            self._started = True
            out += self._start()
        for c in chunks:
            if self._ended:
                break
            if c is _DONE:
                self._ended = True
                out += self._end()
                continue
            if c.get("error"):
                self._ended = True
                message, _, code = _error_fields(500, json.dumps(c).encode())
                out += self._fail(message, code)
                continue
            self._chunk(c, out)
        return b"".join(out)

    def _chunk(self, c: dict, out: list) -> None:
        if not self.model and isinstance(c.get("model"), str):
            self.model = c["model"]
        if isinstance(c.get("usage"), dict):
            self.usage = c["usage"]
        choices = _list(c.get("choices"))
        for ch in choices:
            ch = _dict(ch)
            if _int(ch.get("index")) != 0:
                continue  # n > 1 has no Responses or Messages form; keep the first choice
            delta = _dict(ch.get("delta"))
            reasoning = _reasoning_of(delta)
            if reasoning:
                out += self._reasoning(reasoning)
            text = _parts_text(delta.get("content"), "")
            if text:
                out += self._text(text)
            for pos, tc in enumerate(_list(delta.get("tool_calls"))):
                tc = _dict(tc)
                fn = _dict(tc.get("function"))
                idx = tc.get("index") if isinstance(tc.get("index"), int) else pos
                args = fn.get("arguments")
                out += self._tool(idx, tc.get("id") if isinstance(tc.get("id"), str) else None,
                                  fn.get("name") if isinstance(fn.get("name"), str) else None,
                                  args if isinstance(args, str) else (_str(args) if args is not None else ""))
            if ch.get("finish_reason"):
                self.finish_reason = ch["finish_reason"]

    # hooks, each returning a list of SSE event bytes
    def _start(self) -> list: return []
    def _reasoning(self, text: str) -> list: return []
    def _text(self, text: str) -> list: return []
    def _tool(self, idx: int, call_id: Optional[str], name: Optional[str], args: str) -> list: return []
    def _end(self) -> list: return []
    def _fail(self, message: str, code: Any) -> list: return []


class _Piece:
    """One output item (Responses) or content block (Messages) being streamed."""

    def __init__(self, kind: str, index: int, iid: str) -> None:
        self.kind, self.index, self.id = kind, index, iid
        self.text = ""
        self.call_id = ""
        self.name = ""
        self.done = False


def _event(kind: str, data: dict) -> bytes:
    return f"event: {kind}\ndata: {json.dumps(data)}\n\n".encode()


def _add_name(piece: _Piece, name: Optional[str]) -> None:
    # Most servers send the name once; some repeat it on every chunk, a few send it in pieces.
    if name and name != piece.name:
        piece.name = name if not piece.name else piece.name + name


class ChatToResponsesStream(_ChatStream):
    """Turn a chat SSE stream into Responses API SSE events.

    Items are closed in order: a new item (text after reasoning, the next tool
    call) closes the one before it, the way OpenAI streams them.
    """

    def __init__(self, request: dict) -> None:
        super().__init__(request)
        self.id = "resp_" + _hex()
        self.created = int(time.time())
        self._seq = 0
        self._items: list[_Piece] = []
        self._open: Optional[_Piece] = None
        self._tools: dict[int, _Piece] = {}

    def _ev(self, kind: str, **fields: Any) -> bytes:
        data = {"type": kind, "sequence_number": self._seq, **fields}
        self._seq += 1
        return _event(kind, data)

    def _item(self, p: _Piece, status: str) -> dict:
        if p.kind == "reasoning":
            return _reasoning_item(p.id, p.text) if status == "completed" else {
                "type": "reasoning", "id": p.id, "summary": [], "content": []}
        if p.kind == "message":
            item = _message_item(p.id, p.text, status)
            if status != "completed":
                item["content"] = []
            return item
        return _call_item(p.id, p.call_id, p.name, p.text, status)

    def _response(self, status: str) -> dict:
        output = [self._item(p, "completed") for p in self._items if p.done]
        usage = _responses_usage(self.usage) if self.usage is not None or status != "in_progress" else None
        return _response_obj(self.request, self.id, self.created, self.model, status, output, usage)

    def _new(self, kind: str, prefix: str) -> tuple[_Piece, list]:
        out = self._close()
        p = _Piece(kind, len(self._items), prefix + _hex())
        self._items.append(p)
        self._open = p
        return p, out

    def _close(self) -> list:
        p, self._open = self._open, None
        if p is None or p.done:
            return []
        p.done = True
        out = []
        if p.kind == "reasoning":
            out.append(self._ev("response.reasoning_text.done", item_id=p.id, output_index=p.index,
                                content_index=0, text=p.text))
        elif p.kind == "message":
            out.append(self._ev("response.output_text.done", item_id=p.id, output_index=p.index,
                                content_index=0, text=p.text, logprobs=[]))
            out.append(self._ev("response.content_part.done", item_id=p.id, output_index=p.index,
                                content_index=0, part={"type": "output_text", "text": p.text, "annotations": []}))
        else:
            out.append(self._ev("response.function_call_arguments.done", item_id=p.id, output_index=p.index,
                                name=p.name, arguments=p.text))
        out.append(self._ev("response.output_item.done", output_index=p.index, item=self._item(p, "completed")))
        return out

    def _start(self) -> list:
        return [self._ev("response.created", response=self._response("in_progress")),
                self._ev("response.in_progress", response=self._response("in_progress"))]

    def _reasoning(self, text: str) -> list:
        out: list = []
        p = self._open
        if p is None or p.kind != "reasoning":
            p, out = self._new("reasoning", "rs_")
            out.append(self._ev("response.output_item.added", output_index=p.index, item=self._item(p, "in_progress")))
        p.text += text
        out.append(self._ev("response.reasoning_text.delta", item_id=p.id, output_index=p.index,
                            content_index=0, delta=text))
        return out

    def _text(self, text: str) -> list:
        out: list = []
        p = self._open
        if p is None or p.kind != "message":
            p, out = self._new("message", "msg_")
            out.append(self._ev("response.output_item.added", output_index=p.index, item=self._item(p, "in_progress")))
            out.append(self._ev("response.content_part.added", item_id=p.id, output_index=p.index, content_index=0,
                                part={"type": "output_text", "text": "", "annotations": []}))
        p.text += text
        out.append(self._ev("response.output_text.delta", item_id=p.id, output_index=p.index,
                            content_index=0, delta=text, logprobs=[]))
        return out

    def _tool(self, idx: int, call_id: Optional[str], name: Optional[str], args: str) -> list:
        out: list = []
        p = self._tools.get(idx)
        if p is None:
            p, out = self._new("function_call", "fc_")
            self._tools[idx] = p
            p.call_id = call_id or "call_" + _hex()
            p.name = name or ""
            out.append(self._ev("response.output_item.added", output_index=p.index, item=self._item(p, "in_progress")))
        else:
            _add_name(p, name)
        if args:
            p.text += args
            # A late fragment for a call already closed still lands in the final response.
            if not p.done:
                out.append(self._ev("response.function_call_arguments.delta", item_id=p.id,
                                    output_index=p.index, delta=args))
        return out

    def _end(self) -> list:
        out = self._close()
        if self.finish_reason == "length":
            # OpenAI ends a cut-off answer with response.incomplete, not response.completed.
            out.append(self._ev("response.incomplete", response=self._response("incomplete")))
        else:
            out.append(self._ev("response.completed", response=self._response("completed")))
        return out

    def _fail(self, message: str, code: Any) -> list:
        resp = self._response("failed")
        resp["error"] = {"code": _str(code) if code is not None else "server_error", "message": message}
        return [self._ev("response.failed", response=resp)]


class ChatToMessagesStream(_ChatStream):
    """Turn a chat SSE stream into Anthropic Messages SSE events."""

    def __init__(self, request: dict) -> None:
        super().__init__(request)
        self.id = "msg_" + _hex()
        self._blocks: list[_Piece] = []
        self._open: Optional[_Piece] = None
        self._tools: dict[int, _Piece] = {}

    def _new(self, kind: str, block: dict) -> tuple[_Piece, list]:
        out = self._close()
        p = _Piece(kind, len(self._blocks), "")
        self._blocks.append(p)
        self._open = p
        out.append(_event("content_block_start", {"type": "content_block_start", "index": p.index,
                                                  "content_block": block}))
        return p, out

    def _close(self) -> list:
        p, self._open = self._open, None
        if p is None or p.done:
            return []
        p.done = True
        return [_event("content_block_stop", {"type": "content_block_stop", "index": p.index})]

    def _delta(self, p: _Piece, delta: dict) -> bytes:
        return _event("content_block_delta", {"type": "content_block_delta", "index": p.index, "delta": delta})

    def _start(self) -> list:
        msg = {"id": self.id, "type": "message", "role": "assistant", "model": self.model or "", "content": [],
               "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": 0, "output_tokens": 0}}
        return [_event("message_start", {"type": "message_start", "message": msg})]

    def _reasoning(self, text: str) -> list:
        out: list = []
        p = self._open
        if p is None or p.kind != "thinking":
            p, out = self._new("thinking", {"type": "thinking", "thinking": "", "signature": ""})
        p.text += text
        out.append(self._delta(p, {"type": "thinking_delta", "thinking": text}))
        return out

    def _text(self, text: str) -> list:
        out: list = []
        p = self._open
        if p is None or p.kind != "text":
            p, out = self._new("text", {"type": "text", "text": ""})
        p.text += text
        out.append(self._delta(p, {"type": "text_delta", "text": text}))
        return out

    def _tool(self, idx: int, call_id: Optional[str], name: Optional[str], args: str) -> list:
        out: list = []
        p = self._tools.get(idx)
        if p is None:
            cid = call_id or "toolu_" + _hex()
            p, out = self._new("tool_use", {"type": "tool_use", "id": cid, "name": name or "", "input": {}})
            p.call_id, p.name = cid, name or ""
            self._tools[idx] = p
        else:
            _add_name(p, name)
        if args:
            p.text += args
            if not p.done:
                out.append(self._delta(p, {"type": "input_json_delta", "partial_json": args}))
        return out

    def _end(self) -> list:
        out = self._close()
        usage = _dict(self.usage)
        out.append(_event("message_delta", {
            "type": "message_delta",
            "delta": {"stop_reason": _stop_reason(self.finish_reason, bool(self._tools)), "stop_sequence": None},
            "usage": {"input_tokens": _int(usage.get("prompt_tokens")),
                      "output_tokens": _int(usage.get("completion_tokens"))}}))
        out.append(_event("message_stop", {"type": "message_stop"}))
        return out

    def _fail(self, message: str, code: Any) -> list:
        return [_event("error", {"type": "error", "error": {"type": "api_error", "message": message}})]
