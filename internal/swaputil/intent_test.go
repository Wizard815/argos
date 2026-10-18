package swaputil

import (
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestIntentOfReadsWhatTheCallerDeclared(t *testing.T) {
	for header, want := range map[string]string{
		"prefill":     IntentPrefill,
		"interactive": IntentInteractive,
		"batch":       IntentBatch,
		"PREFILL":     IntentPrefill,
		"  batch  ":   IntentBatch,
	} {
		r := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", nil)
		r.Header.Set(IntentHeader, header)
		if got := IntentOf(r); got != want {
			t.Errorf("IntentOf(%q) = %q, want %q", header, got, want)
		}
	}
}

func TestIntentOfIsEmptyWhenNothingIsDeclared(t *testing.T) {
	r := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", nil)
	if got := IntentOf(r); got != "" {
		t.Errorf("IntentOf(no header) = %q, want empty", got)
	}
	r.Header.Set(IntentHeader, "please-hurry")
	if got := IntentOf(r); got != "" {
		t.Errorf("IntentOf(unknown) = %q, want empty: only what is stated counts", got)
	}
	if got := IntentOf(nil); got != "" {
		t.Errorf("IntentOf(nil) = %q, want empty", got)
	}
}
