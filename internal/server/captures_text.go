package server

import (
	"bytes"
	"strings"

	"github.com/tidwall/gjson"
)

// The functions here pull the readable parts out of a captured request and
// response, so a persisted capture can be searched and turned into training
// data without every consumer knowing each API's JSON shape or reassembling
// streamed chunks. They are best effort: an unknown shape gives "".

// capturePromptText returns what the caller asked: the last user message for
// chat-style APIs (OpenAI chat, Anthropic messages, Responses), the prompt for
// completions, or the input text for embeddings and speech.
func capturePromptText(reqBody []byte) string {
	if len(reqBody) == 0 || !gjson.ValidBytes(reqBody) {
		return ""
	}
	req := gjson.ParseBytes(reqBody)

	if msgs := req.Get("messages"); msgs.IsArray() {
		arr := msgs.Array()
		for i := len(arr) - 1; i >= 0; i-- {
			if arr[i].Get("role").String() == "user" {
				return contentText(arr[i].Get("content"))
			}
		}
		return ""
	}
	if input := req.Get("input"); input.Exists() {
		// Responses API: a string, or a list of messages/content items.
		if input.IsArray() {
			arr := input.Array()
			for i := len(arr) - 1; i >= 0; i-- {
				if role := arr[i].Get("role").String(); role == "user" || role == "" {
					if t := contentText(arr[i].Get("content")); t != "" {
						return t
					}
					if arr[i].Type == gjson.String {
						return arr[i].String()
					}
				}
			}
			return ""
		}
		return input.String()
	}
	if prompt := req.Get("prompt"); prompt.Exists() {
		if prompt.IsArray() {
			return strings.Join(stringItems(prompt), "\n")
		}
		return prompt.String()
	}
	return ""
}

// captureResponseText returns the model's answer from a JSON response or a
// server-sent event stream. When the answer is only tool calls it describes
// them as "[tool] name(arguments)" so they are still searchable.
func captureResponseText(respBody []byte) string {
	body := bytes.TrimSpace(respBody)
	if len(body) == 0 {
		return ""
	}
	if bytes.HasPrefix(body, []byte("data:")) || bytes.HasPrefix(body, []byte("event:")) {
		return streamText(body)
	}
	if !gjson.ValidBytes(body) {
		return ""
	}
	resp := gjson.ParseBytes(body)

	// OpenAI chat
	if msg := resp.Get("choices.0.message"); msg.Exists() {
		if t := contentText(msg.Get("content")); t != "" {
			return t
		}
		return toolCallsText(msg.Get("tool_calls"))
	}
	// OpenAI completions
	if t := resp.Get("choices.0.text"); t.Exists() {
		return t.String()
	}
	// Anthropic messages: content blocks
	if blocks := resp.Get("content"); blocks.IsArray() {
		var parts []string
		for _, b := range blocks.Array() {
			switch b.Get("type").String() {
			case "text":
				parts = append(parts, b.Get("text").String())
			case "tool_use":
				parts = append(parts, "[tool] "+b.Get("name").String()+"("+b.Get("input").Raw+")")
			}
		}
		return strings.Join(parts, "\n")
	}
	// OpenAI Responses
	if t := resp.Get("output_text"); t.Exists() {
		return t.String()
	}
	if out := resp.Get("output"); out.IsArray() {
		var parts []string
		for _, item := range out.Array() {
			for _, c := range item.Get("content").Array() {
				if t := c.Get("text"); t.Exists() {
					parts = append(parts, t.String())
				}
			}
		}
		return strings.Join(parts, "\n")
	}
	// Transcriptions
	if t := resp.Get("text"); t.Type == gjson.String {
		return t.String()
	}
	return ""
}

// streamText reassembles the answer from a server-sent event stream: OpenAI
// chat and completions chunks, Anthropic content_block_delta events and
// Responses output_text deltas. Tool call fragments are joined per call.
func streamText(body []byte) string {
	var text strings.Builder
	type call struct{ name, args strings.Builder }
	var calls []*call

	for line := range bytes.SplitSeq(body, []byte("\n")) {
		data, ok := bytes.CutPrefix(bytes.TrimSpace(line), []byte("data:"))
		if !ok {
			continue
		}
		data = bytes.TrimSpace(data)
		if len(data) == 0 || bytes.Equal(data, []byte("[DONE]")) || !gjson.ValidBytes(data) {
			continue
		}
		ev := gjson.ParseBytes(data)

		if delta := ev.Get("choices.0.delta"); delta.Exists() {
			text.WriteString(delta.Get("content").String())
			for _, tc := range delta.Get("tool_calls").Array() {
				i := int(tc.Get("index").Int())
				for len(calls) <= i {
					calls = append(calls, &call{})
				}
				calls[i].name.WriteString(tc.Get("function.name").String())
				calls[i].args.WriteString(tc.Get("function.arguments").String())
			}
			continue
		}
		if t := ev.Get("choices.0.text"); t.Exists() {
			text.WriteString(t.String())
			continue
		}
		switch ev.Get("type").String() {
		case "content_block_delta":
			text.WriteString(ev.Get("delta.text").String())
		case "response.output_text.delta":
			text.WriteString(ev.Get("delta").String())
		}
	}

	if text.Len() > 0 || len(calls) == 0 {
		return text.String()
	}
	var parts []string
	for _, c := range calls {
		parts = append(parts, "[tool] "+c.name.String()+"("+c.args.String()+")")
	}
	return strings.Join(parts, "\n")
}

// contentText returns a message's text whether content is a plain string or
// a list of parts ({"type":"text","text":...} or {"type":"input_text",...}).
func contentText(content gjson.Result) string {
	if !content.Exists() {
		return ""
	}
	if !content.IsArray() {
		return content.String()
	}
	var parts []string
	for _, p := range content.Array() {
		if p.Type == gjson.String {
			parts = append(parts, p.String())
			continue
		}
		if t := p.Get("text"); t.Exists() {
			parts = append(parts, t.String())
		}
	}
	return strings.Join(parts, "\n")
}

func toolCallsText(calls gjson.Result) string {
	var parts []string
	for _, tc := range calls.Array() {
		parts = append(parts, "[tool] "+tc.Get("function.name").String()+"("+tc.Get("function.arguments").String()+")")
	}
	return strings.Join(parts, "\n")
}

func stringItems(arr gjson.Result) []string {
	var out []string
	for _, v := range arr.Array() {
		out = append(out, v.String())
	}
	return out
}
