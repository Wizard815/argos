package router

import (
	"sync"
	"testing"

	"github.com/Wizard815/Argos/internal/config"
	"github.com/Wizard815/Argos/internal/process"
)

// TestBaseRouter_HandoverWhileReadingIsSafe: a reload's hand-over adds and
// removes processes while other goroutines read the table (the share watch
// calls RunningModels every second). That used to crash the engine with
// "concurrent map iteration and map write".
func TestBaseRouter_HandoverWhileReadingIsSafe(t *testing.T) {
	conf := config.Config{HealthCheckTimeout: 5, Models: map[string]config.ModelConfig{"a": {}, "b": {}}}
	a, bp := newFakeProcess("a"), newFakeProcess("b")
	b := newTestBaseWithConfig(t, conf, map[string]process.Process{"a": a, "b": bp}, &stubPlanner{})
	done := make(chan struct{})
	defer close(done)
	go func() { // the test router reports each step it handles: keep it moving
		for {
			select {
			case <-b.testProcessed:
			case <-done:
				return
			}
		}
	}()
	var wg sync.WaitGroup
	for i := 0; i < 3; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for j := 0; j < 2000; j++ {
				b.RunningModels()
				b.Handles("a")
				b.ProcessLogger("b")
			}
		}()
	}
	for i := 0; i < 200; i++ {
		got := b.DetachRunning([]string{"a"})
		if p, ok := got["a"]; ok {
			b.Adopt(map[string]process.Process{"a": p})
		}
	}
	wg.Wait()
}
