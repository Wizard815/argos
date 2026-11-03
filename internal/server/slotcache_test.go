package server

import (
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"

	"github.com/Wizard815/Argos/internal/logmon"
)

// fakeLlama answers GET /slots and records POST /slots/{id}?action=...
func fakeLlama(t *testing.T) (*httptest.Server, *[]string) {
	var mu sync.Mutex
	calls := &[]string{}
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method == http.MethodGet && r.URL.Path == "/slots" {
			w.Write([]byte(`[{"id":0},{"id":1}]`))
			return
		}
		var body struct{ Filename string }
		json.NewDecoder(r.Body).Decode(&body)
		mu.Lock()
		*calls = append(*calls, r.URL.Path+"?"+r.URL.RawQuery+" "+body.Filename)
		mu.Unlock()
		w.Write([]byte(`{}`))
	}))
	t.Cleanup(srv.Close)
	return srv, calls
}

const coderCmd = "llama-server -m /app/models/q.gguf -c 524288 --parallel 2 -ctk q4_0 -ctv q4_0 --slot-save-path /tmp --temp 0.7"

func TestSlotCache_SavedBeforeARestartAndRestoredWhenItFits(t *testing.T) {
	llama, calls := fakeLlama(t)
	log := logmon.NewWriter(io.Discard)
	saveSlots("coder--chat", llama.URL, coderCmd, log)
	if strings.Join(*calls, "|") != "/slots/0?action=save argos-coder--chat-slot0.bin|/slots/1?action=save argos-coder--chat-slot1.bin" {
		t.Fatalf("saves: %v", *calls)
	}
	*calls = nil
	// a sampling change only: the cache fits, both slots come back
	restoreSlots("coder--chat", llama.URL, strings.Replace(coderCmd, "--temp 0.7", "--temp 0.6 --top-k 20", 1))
	if len(*calls) != 2 || !strings.Contains((*calls)[0], "action=restore") {
		t.Fatalf("restores: %v", *calls)
	}
	*calls = nil
	restoreSlots("coder--chat", llama.URL, coderCmd)
	if len(*calls) != 0 {
		t.Fatal("restored once only")
	}
}

func TestSlotCache_NotRestoredWhenTheCacheWouldNotFit(t *testing.T) {
	llama, calls := fakeLlama(t)
	saveSlots("coder--chat", llama.URL, coderCmd, logmon.NewWriter(io.Discard))
	*calls = nil
	restoreSlots("coder--chat", llama.URL, coderCmd+" --rope-scaling yarn --rope-scale 2 --yarn-orig-ctx 262144")
	if len(*calls) != 0 {
		t.Fatalf("RoPE changed: a saved cache would answer wrong, got %v", *calls)
	}
	saveSlots("plain", llama.URL, "llama-server -m x.gguf", logmon.NewWriter(io.Discard))
	if len(*calls) != 0 {
		t.Fatal("no --slot-save-path: nothing saved")
	}
}

func TestSlotCache_SignatureIgnoresSamplingButNotTheCache(t *testing.T) {
	a := cacheSignature("llama-server -m a.gguf -c 8192 -ctk q8_0 --temp 0.7")
	if a != cacheSignature("llama-server --model a.gguf --ctx-size=8192 --cache-type-k q8_0 --top-p 0.8") {
		t.Fatal("spelling and sampling flags do not change the cache")
	}
	if a == cacheSignature("llama-server -m a.gguf -c 8192 -ctk q4_0") {
		t.Fatal("the cache type does")
	}
}
