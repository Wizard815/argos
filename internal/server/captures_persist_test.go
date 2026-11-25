package server

import (
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strconv"
	"testing"

	"github.com/Wizard815/Argos/internal/config"
	"github.com/Wizard815/Argos/internal/logmon"
)

func recordChat(mm *metricsMonitor, reqHeaders map[string]string) {
	r := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", nil)
	copier := newBodyCopier(httptest.NewRecorder())
	copier.Header().Set("Content-Type", "application/json")
	copier.Header().Set("Set-Cookie", "session=abc")
	copier.WriteHeader(http.StatusOK)
	copier.Write([]byte(`{"usage":{"prompt_tokens":3,"completion_tokens":4}}`))
	mm.record("m", r, copier, captureAll, []byte(`{"model":"m"}`), reqHeaders)
}

func TestServer_CapturePersistedToStore(t *testing.T) {
	// captureBuffer 0: the store is the only copy, so a hit proves persistence.
	mm := newTestMetricsMonitor(t, logmon.NewWriter(io.Discard), 10, 0)
	mm.enableCapturePersistence(0)

	headers := headerMap(http.Header{
		"Authorization":   {"Bearer sk-secret"},
		"X-Hermes-Token":  {"abc"},
		"Content-Type":    {"application/json"},
		"X-Client-Secret": {"shh"},
	})
	redactHeaders(headers)
	recordChat(mm, headers)

	entries := metricsEntries(t, mm)
	if len(entries) != 1 || !entries[0].HasCapture {
		t.Fatalf("entries = %+v, want one with a capture", entries)
	}
	rec, found, err := mm.store.GetCapture(t.Context(), entries[0].ID)
	if err != nil || !found {
		t.Fatalf("GetCapture: found=%v err=%v", found, err)
	}
	if rec.Model != "m" || rec.RespStatusCode != http.StatusOK || string(rec.ReqBody) != `{"model":"m"}` {
		t.Fatalf("persisted capture = %+v", rec)
	}
	for _, h := range []string{"Authorization", "X-Hermes-Token", "X-Client-Secret"} {
		if rec.ReqHeaders[h] != "[REDACTED]" {
			t.Errorf("request header %s = %q, want redacted", h, rec.ReqHeaders[h])
		}
	}
	if rec.ReqHeaders["Content-Type"] != "application/json" {
		t.Errorf("Content-Type should not be redacted: %q", rec.ReqHeaders["Content-Type"])
	}
	if rec.RespHeaders["Set-Cookie"] != "[REDACTED]" {
		t.Errorf("Set-Cookie = %q, want redacted", rec.RespHeaders["Set-Cookie"])
	}

	got := mm.getCaptureByID(entries[0].ID)
	if got == nil || string(got.RespBody) == "" {
		t.Fatalf("getCaptureByID should fall back to the store, got %+v", got)
	}
}

func TestServer_CapturePersistenceRetention(t *testing.T) {
	mm := newTestMetricsMonitor(t, logmon.NewWriter(io.Discard), 10, 5)
	mm.enableCapturePersistence(2)
	for range 4 {
		recordChat(mm, nil)
	}
	recs, err := mm.store.ListCaptures(t.Context(), 0, 100)
	if err != nil {
		t.Fatalf("ListCaptures: %v", err)
	}
	if len(recs) != 2 {
		t.Fatalf("kept %d captures, want 2", len(recs))
	}
}

func TestServer_CaptureListAPI(t *testing.T) {
	cfg := config.Config{Store: &config.Store{Path: "x", Captures: config.StoreCaptures{Enabled: true}}}

	t.Run("off returns 404", func(t *testing.T) {
		s := newTestServer(nil)
		w := httptest.NewRecorder()
		s.ServeHTTP(w, httptest.NewRequest(http.MethodGet, "/api/captures", nil))
		if w.Code != http.StatusNotFound {
			t.Fatalf("status = %d, want 404", w.Code)
		}
	})

	s := newTestServerWithConfig(cfg, nil)
	s.metrics.enableCapturePersistence(cfg.Store.Captures.CaptureRetention())
	for range 3 {
		recordChat(s.metrics, nil)
	}

	get := func(url string) (int, struct {
		Data        []ReqRespCapture `json:"data"`
		NextAfterID int              `json:"next_after_id"`
	}) {
		var body struct {
			Data        []ReqRespCapture `json:"data"`
			NextAfterID int              `json:"next_after_id"`
		}
		w := httptest.NewRecorder()
		s.ServeHTTP(w, httptest.NewRequest(http.MethodGet, url, nil))
		if w.Code == http.StatusOK {
			if err := json.Unmarshal(w.Body.Bytes(), &body); err != nil {
				t.Fatalf("decode %s: %v", url, err)
			}
		}
		return w.Code, body
	}

	code, first := get("/api/captures?limit=2")
	if code != http.StatusOK || len(first.Data) != 2 {
		t.Fatalf("first page: code=%d len=%d", code, len(first.Data))
	}
	code, second := get("/api/captures?limit=2&after_id=" + strconv.Itoa(first.NextAfterID))
	if code != http.StatusOK || len(second.Data) != 1 || second.Data[0].ID <= first.NextAfterID {
		t.Fatalf("second page: code=%d data=%+v", code, second.Data)
	}
	code, done := get("/api/captures?after_id=" + strconv.Itoa(second.NextAfterID))
	if code != http.StatusOK || len(done.Data) != 0 || done.NextAfterID != second.NextAfterID {
		t.Fatalf("caught-up page: code=%d %+v", code, done)
	}
	if code, _ := get("/api/captures?limit=0"); code != http.StatusBadRequest {
		t.Fatalf("limit=0: code=%d, want 400", code)
	}
	if code, _ := get("/api/captures?after_id=-1"); code != http.StatusBadRequest {
		t.Fatalf("after_id=-1: code=%d, want 400", code)
	}
}

func TestServer_SensitiveHeaderPatterns(t *testing.T) {
	for key, want := range map[string]bool{
		"Authorization":    true,
		"X-Api-Key":        true,
		"Api-Key":          true,
		"X-Goog-Api-Key":   true,
		"X-Hass-Token":     true,
		"X-Webhook-Secret": true,
		"X-Session-ID":     false,
		"Content-Type":     false,
		"User-Agent":       false,
	} {
		if got := isSensitiveHeader(key); got != want {
			t.Errorf("isSensitiveHeader(%q) = %v, want %v", key, got, want)
		}
	}
}
