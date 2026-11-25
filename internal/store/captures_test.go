package store

import (
	"context"
	"path/filepath"
	"testing"
	"time"
)

func newCaptureStore(t *testing.T) *Store {
	t.Helper()
	st, err := New("")
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	t.Cleanup(func() { st.Close() })
	return st
}

func TestStore_CaptureRoundTrip(t *testing.T) {
	st := newCaptureStore(t)
	ctx := context.Background()

	act, err := st.InsertActivity(ctx, ActivityLogEntry{
		Model: "qwen", Src: "ip:10.0.0.5", DurationMs: 1234,
		Tokens:   TokenMetrics{InputTokens: 11, OutputTokens: 22, TokensPerSecond: 50},
		Metadata: map[string]string{"client": "faith"},
	})
	if err != nil {
		t.Fatalf("InsertActivity: %v", err)
	}
	in := CaptureRecord{
		ID:             act.ID,
		Timestamp:      time.Unix(1700000000, 0),
		Model:          "qwen",
		ReqPath:        "/v1/chat/completions",
		RespStatusCode: 200,
		PromptText:     "Turn on the kitchen lights",
		ResponseText:   "Done.",
		ReqHeaders:     map[string]string{"Authorization": "[REDACTED]"},
		ReqBody:        `{"model":"qwen"}`,
		RespHeaders:    map[string]string{"Content-Type": "application/json"},
		RespBody:       `{"choices":[]}`,
	}
	if err := st.InsertCapture(ctx, in); err != nil {
		t.Fatalf("InsertCapture: %v", err)
	}
	// A repeat of the same id is ignored, not an error.
	if err := st.InsertCapture(ctx, in); err != nil {
		t.Fatalf("InsertCapture repeat: %v", err)
	}

	got, found, err := st.GetCapture(ctx, act.ID)
	if err != nil || !found {
		t.Fatalf("GetCapture: found=%v err=%v", found, err)
	}
	if got.Model != "qwen" || got.RespStatusCode != 200 || !got.Timestamp.Equal(in.Timestamp) ||
		got.ReqBody != in.ReqBody || got.RespBody != in.RespBody ||
		got.PromptText != in.PromptText || got.ResponseText != in.ResponseText ||
		got.ReqHeaders["Authorization"] != "[REDACTED]" || got.RespHeaders["Content-Type"] != "application/json" {
		t.Fatalf("round trip mismatch: %+v", got)
	}
	// Fields joined from the activity row.
	if got.Src != "ip:10.0.0.5" || got.DurationMs != 1234 || got.Tokens.InputTokens != 11 ||
		got.Tokens.OutputTokens != 22 || got.Metadata["client"] != "faith" {
		t.Fatalf("activity fields not joined: %+v", got)
	}

	if _, found, err := st.GetCapture(ctx, act.ID+1); err != nil || found {
		t.Fatalf("GetCapture(missing): found=%v err=%v, want not found", found, err)
	}
}

func TestStore_CaptureWithoutActivityRow(t *testing.T) {
	st := newCaptureStore(t)
	if err := st.InsertCapture(context.Background(), CaptureRecord{ID: 42, Model: "m"}); err != nil {
		t.Fatalf("InsertCapture: %v", err)
	}
	got, found, err := st.GetCapture(context.Background(), 42)
	if err != nil || !found || got.Model != "m" || got.Src != "" {
		t.Fatalf("orphan capture: found=%v err=%v rec=%+v", found, err, got)
	}
}

func TestStore_CaptureBodyText(t *testing.T) {
	if got := BodyText([]byte(`{"a":"é"}`)); got != `{"a":"é"}` {
		t.Fatalf("UTF-8 body changed: %q", got)
	}
	bin := []byte{0xff, 0x00, 0xfe}
	text := BodyText(bin)
	if text != "base64:/wD+" {
		t.Fatalf("binary body = %q", text)
	}
	if string(BodyBytes(text)) != string(bin) || string(BodyBytes("plain")) != "plain" {
		t.Fatal("BodyBytes does not reverse BodyText")
	}
}

func TestStore_CaptureSearch(t *testing.T) {
	st := newCaptureStore(t)
	ctx := context.Background()
	base := time.Unix(1700000000, 0)
	rows := []CaptureRecord{
		{ID: 2, Model: "qwen", ReqPath: "/v1/chat/completions", RespStatusCode: 200, PromptText: "Turn on the Kitchen lights", ResponseText: "ok"},
		{ID: 3, Model: "qwen", ReqPath: "/v1/chat/completions", RespStatusCode: 500, PromptText: "what is 50% of 10", ResponseText: ""},
		{ID: 5, Model: "llama", ReqPath: "/v1/completions", RespStatusCode: 200, PromptText: "hello", ResponseText: "The garage door is open"},
		{ID: 8, Model: "llama", ReqPath: "/v1/chat/completions", RespStatusCode: 200, ReqBody: `{"tools":[{"name":"hass_light"}]}`},
		{ID: 13, Model: "qwen", ReqPath: "/v1/chat/completions", RespStatusCode: 200, PromptText: "bye", ReqBody: "big body"},
	}
	for i, r := range rows {
		r.Timestamp = base.Add(time.Duration(i) * time.Hour)
		if err := st.InsertCapture(ctx, r); err != nil {
			t.Fatalf("InsertCapture(%d): %v", r.ID, err)
		}
	}

	for _, tc := range []struct {
		name string
		q    CaptureQuery
		want []int
	}{
		{"all oldest first", CaptureQuery{}, []int{2, 3, 5, 8, 13}},
		{"page after id", CaptureQuery{AfterID: 3, Limit: 2}, []int{5, 8}},
		{"newest first", CaptureQuery{Desc: true, Limit: 2}, []int{13, 8}},
		{"newest first before id", CaptureQuery{Desc: true, BeforeID: 8}, []int{5, 3, 2}},
		{"model", CaptureQuery{Model: "llama"}, []int{5, 8}},
		{"path", CaptureQuery{Path: "/v1/completions"}, []int{5}},
		{"status", CaptureQuery{Status: 500}, []int{3}},
		{"since", CaptureQuery{Since: base.Add(3 * time.Hour)}, []int{8, 13}},
		{"until", CaptureQuery{Until: base.Add(time.Hour)}, []int{2, 3}},
		{"text in prompt ignores case", CaptureQuery{Text: "kitchen"}, []int{2}},
		{"text in answer", CaptureQuery{Text: "garage door"}, []int{5}},
		{"text in request body", CaptureQuery{Text: "hass_light"}, []int{8}},
		{"percent is literal", CaptureQuery{Text: "50%"}, []int{3}},
		{"underscore is literal", CaptureQuery{Text: "s_l"}, []int{8}},
		{"filters combine", CaptureQuery{Model: "qwen", Status: 200}, []int{2, 13}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			got, err := st.ListCaptures(ctx, tc.q)
			if err != nil {
				t.Fatalf("ListCaptures: %v", err)
			}
			if !sameIDs(ids(got), tc.want) {
				t.Fatalf("got %v, want %v", ids(got), tc.want)
			}
		})
	}

	got, err := st.ListCaptures(ctx, CaptureQuery{Text: "bye", NoBodies: true})
	if err != nil || len(got) != 1 {
		t.Fatalf("summary view: %v %v", got, err)
	}
	if got[0].ReqBody != "" || got[0].PromptText != "bye" {
		t.Fatalf("summary view should drop bodies and keep text: %+v", got[0])
	}

	found, err := st.CaptureIDs(ctx, []int{1, 2, 13, 99})
	if err != nil || len(found) != 2 || !found[2] || !found[13] {
		t.Fatalf("CaptureIDs = %v, %v", found, err)
	}
}

func TestStore_CapturePrune(t *testing.T) {
	st := newCaptureStore(t)
	ctx := context.Background()
	// Gaps on purpose: capture ids follow activity ids, which not every
	// request turns into a capture.
	for _, id := range []int{2, 3, 5, 8, 13} {
		if err := st.InsertCapture(ctx, CaptureRecord{ID: id}); err != nil {
			t.Fatalf("InsertCapture(%d): %v", id, err)
		}
	}
	if err := st.PruneCaptures(ctx, 10); err != nil {
		t.Fatalf("PruneCaptures(10): %v", err)
	}
	if all, _ := st.ListCaptures(ctx, CaptureQuery{}); len(all) != 5 {
		t.Fatalf("prune above the row count deleted rows: %v", ids(all))
	}
	if err := st.PruneCaptures(ctx, 2); err != nil {
		t.Fatalf("PruneCaptures(2): %v", err)
	}
	all, err := st.ListCaptures(ctx, CaptureQuery{})
	if err != nil || !sameIDs(ids(all), []int{8, 13}) {
		t.Fatalf("after prune to 2 = %v (%v), want [8 13]", ids(all), err)
	}
}

func TestStore_CapturesSurviveReopen(t *testing.T) {
	path := filepath.Join(t.TempDir(), "argos.db")
	st, err := New(path)
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	if err := st.InsertCapture(context.Background(), CaptureRecord{ID: 1, ReqBody: "hi"}); err != nil {
		t.Fatalf("InsertCapture: %v", err)
	}
	st.Close()

	st, err = New(path)
	if err != nil {
		t.Fatalf("reopen: %v", err)
	}
	defer st.Close()
	got, found, err := st.GetCapture(context.Background(), 1)
	if err != nil || !found || got.ReqBody != "hi" {
		t.Fatalf("after reopen: found=%v err=%v rec=%+v", found, err, got)
	}
}

func ids(recs []CaptureRecord) []int {
	out := make([]int, len(recs))
	for i, r := range recs {
		out[i] = r.ID
	}
	return out
}

func sameIDs(a, b []int) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}
