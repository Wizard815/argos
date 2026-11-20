package process

import (
	"context"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os/exec"
	"sync/atomic"
	"testing"
	"time"

	"github.com/Wizard815/Argos/internal/config"
)

func TestProcessCommand_ReadLoadProgress(t *testing.T) {
	llama := readLoadProgress([]byte(`{"error":{"code":503,"message":"Loading model","type":"unavailable_error"}}`))
	if !llama.seen || llama.message != "Loading model" {
		t.Fatalf("llama-server's loading answer: %+v", llama)
	}
	if again := readLoadProgress([]byte(`{"error":{"code":503,"message":"Loading model"}}`)); again.movedOn(llama) {
		t.Error("the same message again restarted the countdown")
	}
	a, b := readLoadProgress([]byte(`{"progress":0.2}`)), readLoadProgress([]byte(`{"progress":0.4}`))
	if !b.movedOn(a) || a.movedOn(b) {
		t.Errorf("progress 0.2 -> 0.4: %v, back: %v", b.movedOn(a), a.movedOn(b))
	}
	if readLoadProgress([]byte(`not json`)).seen || readLoadProgress(nil).seen {
		t.Error("a body that is not JSON counted as progress")
	}
}

// A model whose server keeps reporting progress gets past healthCheckTimeout;
// the countdown only runs out when it stops moving on.
func TestProcessCommand_HealthProgressRestartsTheTimeout(t *testing.T) {
	if _, err := exec.LookPath("sleep"); err != nil {
		t.Skip("needs sleep")
	}
	var calls atomic.Int32
	up := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		n := calls.Add(1)
		if n < 5 { // about 4 s of loading, twice the timeout, with progress each poll
			w.WriteHeader(http.StatusServiceUnavailable)
			fmt.Fprintf(w, `{"error":{"message":"Loading model"},"progress":%g}`, float64(n)/10)
			return
		}
		w.Write([]byte(`{"status":"ok"}`))
	}))
	defer up.Close()

	p := newProcessCommand(t, config.ModelConfig{Cmd: "sleep 30", Proxy: up.URL, CheckEndpoint: "/health", UnloadTimeout: 1})
	done := make(chan error, 1)
	go func() { done <- p.Run(2 * time.Second) }()
	ctx, cancel := contextWithTimeout(10 * time.Second)
	defer cancel()
	if err := p.WaitReady(ctx); err != nil {
		t.Fatalf("not ready after %d polls: %v", calls.Load(), err)
	}
	p.Stop(2 * time.Second)
}

func contextWithTimeout(d time.Duration) (context.Context, context.CancelFunc) {
	return context.WithTimeout(context.Background(), d)
}

func TestProcessCommand_HealthStuckStillTimesOut(t *testing.T) {
	if _, err := exec.LookPath("sleep"); err != nil {
		t.Skip("needs sleep")
	}
	up := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusServiceUnavailable)
		w.Write([]byte(`{"error":{"message":"Loading model"}}`))
	}))
	defer up.Close()
	p := newProcessCommand(t, config.ModelConfig{Cmd: "sleep 30", Proxy: up.URL, CheckEndpoint: "/health", UnloadTimeout: 1})
	start := time.Now()
	err := p.Run(2 * time.Second)
	if err == nil || time.Since(start) > 8*time.Second {
		t.Fatalf("a server that never moves on: err=%v after %v", err, time.Since(start))
	}
}
