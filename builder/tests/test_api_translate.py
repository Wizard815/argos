"""Responses and Anthropic Messages over a Chat Completions backend (api_translate.py)."""
import json

from src.api_translate import (ChatToMessagesStream, ChatToResponsesStream, chat_to_message, chat_to_response,
                               error_to_messages, error_to_responses, messages_to_chat, responses_to_chat)


def sse(*chunks) -> bytes:
    """A chat SSE stream: dicts become data lines, then [DONE]."""
    return b"".join(b"data: " + json.dumps(c).encode() + b"\n\n" for c in chunks) + b"data: [DONE]\n\n"


def delta(**d):
    return {"choices": [{"index": 0, "delta": d}]}


def finish(reason):
    return {"choices": [{"index": 0, "delta": {}, "finish_reason": reason}]}


def tool_delta(index, id=None, name=None, args=""):
    tc = {"index": index, "function": {"arguments": args}}
    if id:
        tc["id"], tc["type"], tc["function"]["name"] = id, "function", name
    return delta(tool_calls=[tc])


def events(raw: bytes) -> list[tuple[str, dict]]:
    """Parse SSE output into (event, data) pairs, checking each frame's shape."""
    out = []
    for frame in raw.decode().split("\n\n"):
        if not frame:
            continue
        lines = frame.split("\n")
        assert len(lines) == 2 and lines[0].startswith("event: ") and lines[1].startswith("data: "), frame
        kind, data = lines[0][7:], json.loads(lines[1][6:])
        assert data["type"] == kind
        out.append((kind, data))
    return out


# A Codex-like turn: text, two tool calls, usage.
STREAM = sse(
    delta(role="assistant", content=""),
    delta(content="Let me "), delta(content="look ü."),
    tool_delta(0, "call_a", "shell", ""), tool_delta(0, args='{"cmd":'), tool_delta(0, args='"ls"}'),
    tool_delta(1, "call_b", "read", ""), tool_delta(1, args='{"path":"a.txt"}'),
    finish("tool_calls"),
    {"choices": [], "usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10}},
)


def run_split(conv, raw: bytes, step: int = 1) -> bytes:
    out = b""
    for i in range(0, len(raw), step):
        out += conv.feed(raw[i:i + step])
    return out + conv.close()


# ------------------------------------------------------------ Responses requests


def test_Translate_responses_string_input_and_params():
    chat = responses_to_chat({"model": "m", "input": "hi", "instructions": "be brief", "max_output_tokens": 50,
                              "temperature": 0.2, "top_p": 0.9, "seed": 1, "user": "u", "stream": True,
                              "store": False, "reasoning": {"effort": "high"}, "include": ["x"],
                              "metadata": {"a": "b"}, "truncation": "auto", "previous_response_id": "resp_1"})
    assert chat == {"model": "m", "messages": [{"role": "system", "content": "be brief"},
                                               {"role": "user", "content": "hi"}],
                    "max_tokens": 50, "temperature": 0.2, "top_p": 0.9, "seed": 1, "user": "u",
                    "stream": True, "stream_options": {"include_usage": True}}


def test_Translate_responses_message_items_and_parts():
    chat = responses_to_chat({"model": "m", "input": [
        {"role": "developer", "content": "dev rules"},
        {"type": "message", "role": "user", "content": [
            {"type": "input_text", "text": "look"},
            {"type": "input_image", "image_url": "data:image/png;base64,AAA", "detail": "low"},
            {"type": "input_image", "image_url": {"url": "https://x/y.png"}},
            {"type": "input_image", "file_id": "file_1"}]},
        {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "a"},
                                                              {"type": "output_text", "text": "b"}]},
        {"type": "reasoning", "id": "rs_1", "summary": []},
        {"role": "user", "content": [{"type": "input_text", "text": "next"}]},
    ]})
    assert chat["messages"] == [
        {"role": "system", "content": "dev rules"},
        {"role": "user", "content": [
            {"type": "text", "text": "look"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA", "detail": "low"}},
            {"type": "image_url", "image_url": {"url": "https://x/y.png"}}]},
        {"role": "assistant", "content": "a\nb"},
        {"role": "user", "content": "next"},
    ]
    assert "stream" not in chat and "stream_options" not in chat


def test_Translate_responses_function_calls_merge():
    chat = responses_to_chat({"model": "m", "input": [
        {"type": "message", "role": "user", "content": "go"},
        {"type": "function_call", "call_id": "c1", "name": "f", "arguments": '{"a":1}'},
        {"type": "function_call", "call_id": "c2", "name": "g", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "c1", "output": "one"},
        {"type": "function_call_output", "call_id": "c2",
         "output": [{"type": "input_text", "text": "two"}]},
        {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "then"}]},
        {"type": "function_call", "call_id": "c3", "name": "f", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "c3", "output": {"ok": True}},
    ]})
    m = chat["messages"]
    assert [x["role"] for x in m] == ["user", "assistant", "tool", "tool", "assistant", "tool"]
    assert m[1] == {"role": "assistant", "content": None, "tool_calls": [
        {"id": "c1", "type": "function", "function": {"name": "f", "arguments": '{"a":1}'}},
        {"id": "c2", "type": "function", "function": {"name": "g", "arguments": "{}"}}]}
    assert m[2] == {"role": "tool", "tool_call_id": "c1", "content": "one"}
    assert m[3]["content"] == "two"
    assert m[4]["content"] == "then" and m[4]["tool_calls"][0]["id"] == "c3", "text and its calls are one turn"
    assert m[5]["content"] == '{"ok": true}'


def test_Translate_responses_tools_and_tool_choice():
    tools = [{"type": "function", "name": "f", "description": "does f", "strict": True,
              "parameters": {"type": "object", "properties": {"a": {"type": "string"}}}},
             {"type": "web_search"}, {"type": "custom", "name": "apply_patch"}]
    chat = responses_to_chat({"model": "m", "input": "x", "tools": tools, "tool_choice": "required",
                              "parallel_tool_calls": True})
    assert chat["tools"] == [{"type": "function", "function": {
        "name": "f", "description": "does f", "parameters": {"type": "object", "properties": {"a": {"type": "string"}}}}}]
    assert chat["tool_choice"] == "required" and chat["parallel_tool_calls"] is True
    for choice in ("auto", "none"):
        assert responses_to_chat({"input": "x", "tools": tools, "tool_choice": choice})["tool_choice"] == choice
    named = responses_to_chat({"input": "x", "tools": tools, "tool_choice": {"type": "function", "name": "f"}})
    assert named["tool_choice"] == {"type": "function", "function": {"name": "f"}}
    only_hosted = responses_to_chat({"input": "x", "tools": [{"type": "web_search"}], "tool_choice": "auto",
                                     "parallel_tool_calls": False})
    assert "tools" not in only_hosted and "tool_choice" not in only_hosted
    assert "parallel_tool_calls" not in only_hosted


def test_Translate_responses_text_format():
    schema = {"type": "object"}
    chat = responses_to_chat({"input": "x", "text": {"format": {"type": "json_schema", "name": "out",
                                                                "schema": schema, "strict": True}}})
    assert chat["response_format"] == {"type": "json_schema",
                                       "json_schema": {"name": "out", "schema": schema, "strict": True}}
    chat = responses_to_chat({"input": "x", "text": {"format": {"type": "json_object"}}})
    assert chat["response_format"] == {"type": "json_object"}
    assert "response_format" not in responses_to_chat({"input": "x", "text": {"format": {"type": "text"}}})


def test_Translate_codex_like_request():
    req = {
        "model": "gpt-5-codex", "instructions": "You are Codex.", "stream": True, "store": False,
        "reasoning": {"effort": "medium"}, "include": ["reasoning.encrypted_content"],
        "tools": [{"type": "function", "name": "shell", "parameters": {"type": "object"}, "strict": False}],
        "tool_choice": "auto", "parallel_tool_calls": False,
        "input": [
            {"type": "message", "role": "developer", "content": [{"type": "input_text", "text": "sandbox: ro"}]},
            {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "<env>"},
                                                            {"type": "input_text", "text": "list files"}]},
            {"type": "reasoning", "summary": [], "encrypted_content": "zzz"},
            {"type": "function_call", "name": "shell", "arguments": '{"command":["ls"]}', "call_id": "call_1"},
            {"type": "function_call_output", "call_id": "call_1", "output": "a.txt\n"},
        ],
    }
    chat = responses_to_chat(req)
    assert chat["messages"] == [
        {"role": "system", "content": "You are Codex."},
        {"role": "system", "content": "sandbox: ro"},
        {"role": "user", "content": "<env>\nlist files"},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "call_1", "type": "function", "function": {"name": "shell", "arguments": '{"command":["ls"]}'}}]},
        {"role": "tool", "tool_call_id": "call_1", "content": "a.txt\n"},
    ]
    # every tool message answers a call made just before it
    calls = set()
    for m in chat["messages"]:
        calls |= {c["id"] for c in m.get("tool_calls", [])}
        if m["role"] == "tool":
            assert m["tool_call_id"] in calls
    assert chat["parallel_tool_calls"] is False and chat["tool_choice"] == "auto"
    assert chat["stream_options"] == {"include_usage": True}
    json.dumps(chat)


def test_Translate_responses_odd_input_does_not_raise():
    assert responses_to_chat({})["messages"] == []
    chat = responses_to_chat({"input": [None, 3, {"type": "mystery"}, {"type": "message", "content": None}]})
    assert chat["messages"] == [{"role": "user", "content": ""}]


# ------------------------------------------------------------ Responses answers


def test_Translate_chat_to_response_text():
    chat = {"id": "x", "model": "backend-m", "created": 123,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "hello"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7}}
    r = chat_to_response(chat, {"model": "m", "instructions": "i", "temperature": 0.5})
    assert r["id"].startswith("resp_") and r["object"] == "response" and r["created_at"] == 123
    assert r["status"] == "completed" and r["model"] == "m" and r["output_text"] == "hello"
    assert r["instructions"] == "i" and r["temperature"] == 0.5 and r["top_p"] == 1.0
    assert r["tools"] == [] and r["tool_choice"] == "auto" and r["parallel_tool_calls"] is True
    [item] = r["output"]
    assert item["type"] == "message" and item["id"].startswith("msg_") and item["status"] == "completed"
    assert item["role"] == "assistant"
    assert item["content"] == [{"type": "output_text", "text": "hello", "annotations": []}]
    assert r["usage"]["input_tokens"] == 5 and r["usage"]["output_tokens"] == 2 and r["usage"]["total_tokens"] == 7


def test_Translate_chat_to_response_tool_calls_and_reasoning():
    chat = {"choices": [{"message": {"content": None, "reasoning_content": "think", "tool_calls": [
        {"id": "call_1", "type": "function", "function": {"name": "f", "arguments": '{"a":1}'}},
        {"id": "call_2", "type": "function", "function": {"name": "g", "arguments": "{}"}}]},
        "finish_reason": "tool_calls"}]}
    r = chat_to_response(chat, {"model": "m"})
    assert [o["type"] for o in r["output"]] == ["reasoning", "function_call", "function_call"]
    assert r["output"][0]["content"] == [{"type": "reasoning_text", "text": "think"}]
    assert r["output"][0]["id"].startswith("rs_") and r["output"][0]["summary"] == []
    fc = r["output"][1]
    assert fc["id"].startswith("fc_") and fc["call_id"] == "call_1" and fc["name"] == "f"
    assert fc["arguments"] == '{"a":1}' and fc["status"] == "completed"
    assert r["output_text"] == "" and r["usage"]["total_tokens"] == 0


def test_Translate_chat_to_response_length_and_junk():
    r = chat_to_response({"choices": [{"message": {"content": "cut"}, "finish_reason": "length"}]}, {})
    assert r["status"] == "incomplete" and r["incomplete_details"] == {"reason": "max_output_tokens"}
    r = chat_to_response({"weird": True}, {"model": "m"})
    assert r["output"] == [] and r["status"] == "completed"
    assert chat_to_response(None, None)["output"] == []


# ------------------------------------------------------------ Responses streams


def check_responses_stream(raw: bytes):
    evs = events(raw)
    seqs = [d["sequence_number"] for _, d in evs]
    assert seqs == list(range(len(evs)))
    assert [k for k, _ in evs] == (
        ["response.created", "response.in_progress",
         "response.output_item.added", "response.content_part.added"]
        + ["response.output_text.delta"] * 2
        + ["response.output_text.done", "response.content_part.done", "response.output_item.done",
           "response.output_item.added"] + ["response.function_call_arguments.delta"] * 2
        + ["response.function_call_arguments.done", "response.output_item.done",
           "response.output_item.added", "response.function_call_arguments.delta",
           "response.function_call_arguments.done", "response.output_item.done",
           "response.completed"])
    text = "".join(d["delta"] for k, d in evs if k == "response.output_text.delta")
    assert text == "Let me look ü."
    args = {}
    for k, d in evs:
        if k == "response.function_call_arguments.delta":
            args[d["output_index"]] = args.get(d["output_index"], "") + d["delta"]
    assert args == {1: '{"cmd":"ls"}', 2: '{"path":"a.txt"}'}
    final = evs[-1][1]["response"]
    assert final["status"] == "completed" and final["output_text"] == text
    assert final["id"] == evs[0][1]["response"]["id"]
    assert [o["type"] for o in final["output"]] == ["message", "function_call", "function_call"]
    assert [(o.get("call_id"), o.get("name"), o.get("arguments")) for o in final["output"][1:]] == [
        ("call_a", "shell", '{"cmd":"ls"}'), ("call_b", "read", '{"path":"a.txt"}')]
    assert final["usage"]["input_tokens"] == 7 and final["usage"]["output_tokens"] == 3
    added = [d for k, d in evs if k == "response.output_item.added"]
    assert [d["output_index"] for d in added] == [0, 1, 2]
    assert added[1]["item"]["status"] == "in_progress" and added[1]["item"]["arguments"] == ""
    done = [d["item"] for k, d in evs if k == "response.output_item.done"]
    assert done == final["output"]


def test_Translate_responses_stream_whole():
    conv = ChatToResponsesStream({"model": "m"})
    check_responses_stream(conv.feed(STREAM) + conv.close())


def test_Translate_responses_stream_split_every_byte():
    check_responses_stream(run_split(ChatToResponsesStream({"model": "m"}), STREAM))


def test_Translate_responses_stream_crlf_and_junk_lines():
    raw = STREAM.replace(b"\n", b"\r\n")
    raw = b": keepalive\r\n\r\ndata: not json\r\n\r\nevent: x\r\n" + raw
    check_responses_stream(run_split(ChatToResponsesStream({"model": "m"}), raw, 3))


def test_Translate_responses_stream_reasoning_then_text():
    raw = sse(delta(reasoning_content="hm"), delta(reasoning_content="m"), delta(content="ok"), finish("stop"))
    conv = ChatToResponsesStream({"model": "m"})
    evs = events(conv.feed(raw) + conv.close())
    kinds = [k for k, _ in evs]
    assert kinds[2:6] == ["response.output_item.added", "response.reasoning_text.delta",
                          "response.reasoning_text.delta", "response.reasoning_text.done"]
    assert kinds[6] == "response.output_item.done"
    final = evs[-1][1]["response"]
    assert final["output"][0]["content"] == [{"type": "reasoning_text", "text": "hmm"}]
    assert final["output"][1]["content"][0]["text"] == "ok"


def test_Translate_responses_stream_without_done_and_length():
    conv = ChatToResponsesStream({"model": "m"})
    out = conv.feed(b'data: {"choices":[{"delta":{"content":"x"},"finish_reason":"length"}]}\n\n')
    out += conv.close()
    evs = events(out)
    assert evs[-1][0] == "response.incomplete"
    assert evs[-1][1]["response"]["incomplete_details"] == {"reason": "max_output_tokens"}
    assert conv.close() == b"" and conv.feed(b"data: [DONE]\n\n") == b""


def test_Translate_responses_stream_error():
    conv = ChatToResponsesStream({"model": "m"})
    raw = (b'data: {"choices":[{"delta":{"content":"x"}}]}\n\n'
           b'data: {"error":{"message":"boom","code":"overloaded"}}\n\n'
           b'data: {"choices":[{"delta":{"content":"late"}}]}\n\n')
    evs = events(conv.feed(raw) + conv.close())
    assert evs[-1][0] == "response.failed"
    resp = evs[-1][1]["response"]
    assert resp["status"] == "failed" and resp["error"] == {"code": "overloaded", "message": "boom"}
    assert not any(k == "response.completed" for k, _ in evs)


def test_Translate_responses_stream_plain_json_answer():
    # A backend that ignored stream=true and sent one JSON answer.
    body = json.dumps({"choices": [{"message": {"content": "hi", "tool_calls": [
        {"id": "c", "function": {"name": "f", "arguments": "{}"}}]}, "finish_reason": "tool_calls"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1}}).encode()
    conv = ChatToResponsesStream({"model": "m"})
    evs = events(conv.feed(body) + conv.close())
    final = evs[-1][1]["response"]
    assert evs[-1][0] == "response.completed"
    assert [o["type"] for o in final["output"]] == ["message", "function_call"]


# ------------------------------------------------------------ Anthropic requests


def test_Translate_messages_request():
    req = {"model": "claude-x", "max_tokens": 100, "temperature": 0.1, "top_p": 0.8, "stream": True,
           "stop_sequences": ["END"], "metadata": {"user_id": "u1"},
           "system": [{"type": "text", "text": "sys a", "cache_control": {"type": "ephemeral"}},
                      {"type": "text", "text": "sys b"}],
           "tools": [{"name": "f", "description": "d", "input_schema": {"type": "object"}},
                     {"type": "web_search_20250305", "name": "web_search"}],
           "tool_choice": {"type": "any", "disable_parallel_tool_use": True},
           "messages": [
               {"role": "user", "content": [
                   {"type": "text", "text": "see"},
                   {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "QUJD"}},
                   {"type": "image", "source": {"type": "url", "url": "https://x/i.png"}}]},
               {"role": "assistant", "content": [
                   {"type": "thinking", "thinking": "hmm", "signature": "s"},
                   {"type": "text", "text": "calling"},
                   {"type": "tool_use", "id": "toolu_1", "name": "f", "input": {"a": 1}},
                   {"type": "tool_use", "id": "toolu_2", "name": "f", "input": {}}]},
               {"role": "user", "content": [
                   {"type": "text", "text": "and also"},
                   {"type": "tool_result", "tool_use_id": "toolu_1", "content": "r1"},
                   {"type": "tool_result", "tool_use_id": "toolu_2",
                    "content": [{"type": "text", "text": "r2a"}, {"type": "text", "text": "r2b"}]}]},
               {"role": "assistant", "content": "done"},
           ]}
    chat = messages_to_chat(req)
    assert chat["messages"] == [
        {"role": "system", "content": "sys a\nsys b"},
        {"role": "user", "content": [
            {"type": "text", "text": "see"},
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,QUJD"}},
            {"type": "image_url", "image_url": {"url": "https://x/i.png"}}]},
        {"role": "assistant", "content": "calling", "tool_calls": [
            {"id": "toolu_1", "type": "function", "function": {"name": "f", "arguments": '{"a": 1}'}},
            {"id": "toolu_2", "type": "function", "function": {"name": "f", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "toolu_1", "content": "r1"},
        {"role": "tool", "tool_call_id": "toolu_2", "content": "r2a\nr2b"},
        {"role": "user", "content": "and also"},
        {"role": "assistant", "content": "done"},
    ]
    assert chat["tools"] == [{"type": "function", "function": {"name": "f", "description": "d",
                                                               "parameters": {"type": "object"}}}]
    assert chat["tool_choice"] == "required" and chat["parallel_tool_calls"] is False
    assert chat["max_tokens"] == 100 and chat["temperature"] == 0.1 and chat["top_p"] == 0.8
    assert chat["stop"] == ["END"] and chat["user"] == "u1"
    assert chat["stream"] is True and chat["stream_options"] == {"include_usage": True}


def test_Translate_messages_tool_choice():
    tools = [{"name": "f", "input_schema": {"type": "object"}}]
    for given, want in [({"type": "auto"}, "auto"), ({"type": "none"}, "none"),
                        ({"type": "tool", "name": "f"}, {"type": "function", "function": {"name": "f"}})]:
        assert messages_to_chat({"messages": [], "tools": tools, "tool_choice": given})["tool_choice"] == want
    assert messages_to_chat({"system": "s", "messages": [{"role": "user", "content": "hi"}]})["messages"] == [
        {"role": "system", "content": "s"}, {"role": "user", "content": "hi"}]


def test_Translate_messages_odd_input_does_not_raise():
    assert messages_to_chat({})["messages"] == []
    messages_to_chat({"messages": [None, {"role": "user", "content": [None, {"type": "image"}]}]})


# ------------------------------------------------------------ Anthropic answers


def test_Translate_chat_to_message():
    chat = {"choices": [{"message": {"content": "hi", "reasoning_content": "r", "tool_calls": [
        {"id": "call_1", "function": {"name": "f", "arguments": '{"a":1}'}},
        {"id": "call_2", "function": {"name": "g", "arguments": "not json"}}]}, "finish_reason": "tool_calls"}],
        "usage": {"prompt_tokens": 4, "completion_tokens": 6}}
    m = chat_to_message(chat, {"model": "claude-x"})
    assert m["id"].startswith("msg_") and m["type"] == "message" and m["role"] == "assistant"
    assert m["model"] == "claude-x" and m["stop_sequence"] is None
    assert m["content"] == [{"type": "thinking", "thinking": "r", "signature": ""},
                            {"type": "text", "text": "hi"},
                            {"type": "tool_use", "id": "call_1", "name": "f", "input": {"a": 1}},
                            {"type": "tool_use", "id": "call_2", "name": "g", "input": {}}]
    assert m["stop_reason"] == "tool_use" and m["usage"] == {"input_tokens": 4, "output_tokens": 6}
    for reason, want in [("stop", "end_turn"), ("length", "max_tokens"), (None, "end_turn")]:
        got = chat_to_message({"choices": [{"message": {"content": "x"}, "finish_reason": reason}]}, {})
        assert got["stop_reason"] == want


def test_Translate_messages_stream_split_every_byte():
    raw = sse(delta(reasoning_content="th"), *[c for c in [
        delta(content="Let me "), delta(content="look ü."),
        tool_delta(0, "call_a", "shell", ""), tool_delta(0, args='{"cmd":'), tool_delta(0, args='"ls"}'),
        tool_delta(1, "call_b", "read", ""), tool_delta(1, args='{"path":"a.txt"}'),
        finish("tool_calls"), {"choices": [], "usage": {"prompt_tokens": 7, "completion_tokens": 3}}]])
    evs = events(run_split(ChatToMessagesStream({"model": "claude-x"}), raw))
    assert [k for k, _ in evs] == (
        ["message_start",
         "content_block_start", "content_block_delta", "content_block_stop",
         "content_block_start", "content_block_delta", "content_block_delta", "content_block_stop",
         "content_block_start", "content_block_delta", "content_block_delta", "content_block_stop",
         "content_block_start", "content_block_delta", "content_block_stop",
         "message_delta", "message_stop"])
    assert evs[0][1]["message"]["model"] == "claude-x"
    starts = [d for k, d in evs if k == "content_block_start"]
    assert [d["index"] for d in starts] == [0, 1, 2, 3]
    assert [d["content_block"]["type"] for d in starts] == ["thinking", "text", "tool_use", "tool_use"]
    assert starts[2]["content_block"] == {"type": "tool_use", "id": "call_a", "name": "shell", "input": {}}
    by_index: dict = {}
    for k, d in evs:
        if k == "content_block_delta":
            dl = d["delta"]
            piece = dl.get("text") or dl.get("thinking") or dl.get("partial_json")
            by_index[d["index"]] = by_index.get(d["index"], "") + piece
    assert by_index == {0: "th", 1: "Let me look ü.", 2: '{"cmd":"ls"}', 3: '{"path":"a.txt"}'}
    assert [d["delta"]["type"] for k, d in evs if k == "content_block_delta"][:2] == ["thinking_delta", "text_delta"]
    md = evs[-2][1]
    assert md["delta"] == {"stop_reason": "tool_use", "stop_sequence": None}
    assert md["usage"]["output_tokens"] == 3


def test_Translate_messages_stream_error_and_close():
    conv = ChatToMessagesStream({"model": "m"})
    evs = events(conv.feed(b'data: {"error":{"message":"nope"}}\n\n') + conv.close())
    assert [k for k, _ in evs] == ["message_start", "error"]
    assert evs[1][1]["error"] == {"type": "api_error", "message": "nope"}

    conv = ChatToMessagesStream({"model": "m"})
    evs = events(conv.feed(b'data: {"choices":[{"delta":{"content":"x"},"finish_reason":"length"}]}\n')
                 + conv.close())
    assert [k for k, _ in evs][-3:] == ["content_block_stop", "message_delta", "message_stop"]
    assert evs[-2][1]["delta"]["stop_reason"] == "max_tokens"


# ---------------------------------------------------------------------- errors


def test_Translate_errors():
    body = json.dumps({"error": {"message": "bad model", "type": "invalid_request_error", "code": "model"}}).encode()
    assert error_to_responses(400, body) == {"error": {"message": "bad model", "type": "invalid_request_error",
                                                       "code": "model"}}
    assert error_to_messages(400, body) == {"type": "error", "error": {"type": "invalid_request_error",
                                                                       "message": "bad model"}}
    assert error_to_responses(502, b"Bad Gateway")["error"] == {"message": "Bad Gateway", "type": "server_error",
                                                                 "code": None}
    assert error_to_messages(429, b'{"error":"slow down"}')["error"] == {"type": "rate_limit_error",
                                                                         "message": "slow down"}
    assert error_to_messages(503, b'{"detail":"loading"}')["error"]["type"] == "overloaded_error"
    assert error_to_messages(500, b"")["error"]["message"] == "upstream error 500"
    assert error_to_responses(500, b"[1,2]")["error"]["message"] == "upstream error 500"
