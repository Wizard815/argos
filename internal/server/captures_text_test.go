package server

import "testing"

func TestServer_CapturePromptText(t *testing.T) {
	for name, tc := range map[string]struct{ body, want string }{
		"chat last user message": {`{"messages":[{"role":"system","content":"be nice"},{"role":"user","content":"first"},{"role":"assistant","content":"a"},{"role":"user","content":"second"}]}`, "second"},
		"chat content parts":     {`{"messages":[{"role":"user","content":[{"type":"text","text":"look"},{"type":"image_url","image_url":{"url":"x"}}]}]}`, "look"},
		"completions":            {`{"prompt":"once upon"}`, "once upon"},
		"responses string":       {`{"input":"hello there"}`, "hello there"},
		"responses items":        {`{"input":[{"role":"user","content":[{"type":"input_text","text":"hi"}]}]}`, "hi"},
		"speech input":           {`{"input":"read this aloud","voice":"af"}`, "read this aloud"},
		"not json":               {`garbage`, ""},
		"empty":                  {``, ""},
	} {
		if got := capturePromptText([]byte(tc.body)); got != tc.want {
			t.Errorf("%s: got %q, want %q", name, got, tc.want)
		}
	}
}

func TestServer_CaptureResponseText(t *testing.T) {
	for name, tc := range map[string]struct{ body, want string }{
		"chat":            {`{"choices":[{"message":{"role":"assistant","content":"Lights on."}}]}`, "Lights on."},
		"chat tool calls": {`{"choices":[{"message":{"content":null,"tool_calls":[{"function":{"name":"hass_turn_on","arguments":"{\"entity\":\"light.kitchen\"}"}}]}}]}`, `[tool] hass_turn_on({"entity":"light.kitchen"})`},
		"completions":     {`{"choices":[{"text":" a time"}]}`, " a time"},
		"anthropic":       {`{"content":[{"type":"text","text":"Sure."},{"type":"tool_use","name":"lookup","input":{"q":1}}]}`, "Sure.\n[tool] lookup({\"q\":1})"},
		"responses":       {`{"output":[{"content":[{"type":"output_text","text":"Hi!"}]}]}`, "Hi!"},
		"transcription":   {`{"text":"what time is it"}`, "what time is it"},
		"chat stream": {"data: {\"choices\":[{\"delta\":{\"role\":\"assistant\"}}]}\n\n" +
			"data: {\"choices\":[{\"delta\":{\"content\":\"Hel\"}}]}\n\n" +
			"data: {\"choices\":[{\"delta\":{\"content\":\"lo\"}}]}\n\ndata: [DONE]\n\n", "Hello"},
		"chat stream tool call": {"data: {\"choices\":[{\"delta\":{\"tool_calls\":[{\"index\":0,\"function\":{\"name\":\"hass\",\"arguments\":\"{\\\"a\\\"\"}}]}}]}\n\n" +
			"data: {\"choices\":[{\"delta\":{\"tool_calls\":[{\"index\":0,\"function\":{\"arguments\":\":1}\"}}]}}]}\n\n", `[tool] hass({"a":1})`},
		"anthropic stream": {"event: content_block_delta\ndata: {\"type\":\"content_block_delta\",\"delta\":{\"text\":\"Yo\"}}\n\n", "Yo"},
		"not json":         {`<html>`, ""},
	} {
		if got := captureResponseText([]byte(tc.body)); got != tc.want {
			t.Errorf("%s: got %q, want %q", name, got, tc.want)
		}
	}
}
