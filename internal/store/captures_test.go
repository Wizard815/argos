package store

import (
	"context"
	"path/filepath"
	"testing"
	"time"
)

func TestStore_CaptureRoundTrip(t *testing.T) {
	st, err := New("")
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	defer st.Close()
	ctx := context.Background()

	in := CaptureRecord{
		ID:             7,
		Timestamp:      time.Unix(1700000000, 0),
		Model:          "qwen",
		ReqPath:        "/v1/chat/completions",
		RespStatusCode: 200,
		ReqHeaders:     map[string]string{"Authorization": "[REDACTED]"},
		ReqBody:        []byte(`{"model":"qwen"}`),
		RespHeaders:    map[string]string{"Content-Type": "application/json"},
		RespBody:       []byte(`{"choices":[]}`),
	}
	if err := st.InsertCapture(ctx, in); err != nil {
		t.Fatalf("InsertCapture: %v", err)
	}
	// A repeat of the same id is ignored, not an error.
	if err := st.InsertCapture(ctx, in); err != nil {
		t.Fatalf("InsertCapture repeat: %v", err)
	}

	got, found, err := st.GetCapture(ctx, 7)
	if err != nil || !found {
		t.Fatalf("GetCapture: found=%v err=%v", found, err)
	}
	if got.Model != "qwen" || got.RespStatusCode != 200 || !got.Timestamp.Equal(in.Timestamp) ||
		string(got.ReqBody) != string(in.ReqBody) || string(got.RespBody) != string(in.RespBody) ||
		got.ReqHeaders["Authorization"] != "[REDACTED]" || got.RespHeaders["Content-Type"] != "application/json" {
		t.Fatalf("round trip mismatch: %+v", got)
	}

	if _, found, err := st.GetCapture(ctx, 8); err != nil || found {
		t.Fatalf("GetCapture(8): found=%v err=%v, want not found", found, err)
	}
}

func TestStore_CaptureListAndPrune(t *testing.T) {
	st, err := New("")
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	defer st.Close()
	ctx := context.Background()

	// Gaps on purpose: capture ids follow activity ids, which not every
	// request turns into a capture.
	for _, id := range []int{2, 3, 5, 8, 13} {
		if err := st.InsertCapture(ctx, CaptureRecord{ID: id, ReqPath: "/v1/x"}); err != nil {
			t.Fatalf("InsertCapture(%d): %v", id, err)
		}
	}

	page, err := st.ListCaptures(ctx, 3, 2)
	if err != nil {
		t.Fatalf("ListCaptures: %v", err)
	}
	if len(page) != 2 || page[0].ID != 5 || page[1].ID != 8 {
		t.Fatalf("ListCaptures(after 3, limit 2) = %v", ids(page))
	}

	found, err := st.CaptureIDs(ctx, []int{1, 2, 13, 99})
	if err != nil {
		t.Fatalf("CaptureIDs: %v", err)
	}
	if len(found) != 2 || !found[2] || !found[13] {
		t.Fatalf("CaptureIDs = %v", found)
	}

	if err := st.PruneCaptures(ctx, 10); err != nil {
		t.Fatalf("PruneCaptures(10): %v", err)
	}
	if all, _ := st.ListCaptures(ctx, 0, 100); len(all) != 5 {
		t.Fatalf("prune above the row count deleted rows: %v", ids(all))
	}

	if err := st.PruneCaptures(ctx, 2); err != nil {
		t.Fatalf("PruneCaptures(2): %v", err)
	}
	all, err := st.ListCaptures(ctx, 0, 100)
	if err != nil {
		t.Fatalf("ListCaptures: %v", err)
	}
	if len(all) != 2 || all[0].ID != 8 || all[1].ID != 13 {
		t.Fatalf("after prune to 2 = %v, want [8 13]", ids(all))
	}
}

func TestStore_CapturesSurviveReopen(t *testing.T) {
	path := filepath.Join(t.TempDir(), "argos.db")
	st, err := New(path)
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	if err := st.InsertCapture(context.Background(), CaptureRecord{ID: 1, ReqBody: []byte("hi")}); err != nil {
		t.Fatalf("InsertCapture: %v", err)
	}
	st.Close()

	st, err = New(path)
	if err != nil {
		t.Fatalf("reopen: %v", err)
	}
	defer st.Close()
	got, found, err := st.GetCapture(context.Background(), 1)
	if err != nil || !found || string(got.ReqBody) != "hi" {
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
