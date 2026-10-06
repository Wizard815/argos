package router

import (
	"testing"
	"time"

	"github.com/mostlygeek/llama-swap/internal/process"
)

// A reload carries over children whose configuration did not change. DetachRunning
// hands them back and removes them from this router's table, so the shutdown that
// follows leaves them running for the replacement router to adopt.
func TestBaseRouter_DetachRunningHandsBackLiveProcesses(t *testing.T) {
	keep := newFakeProcess("keep")
	keep.markReady()
	drop := newFakeProcess("drop")
	drop.markReady()

	b := newTestBase(t, map[string]process.Process{"keep": keep, "drop": drop}, &stubPlanner{})

	detached := b.DetachRunning([]string{"keep"})
	if len(detached) != 1 {
		t.Fatalf("detached=%d entries want 1", len(detached))
	}
	if got := detached["keep"].State(); got != process.StateReady {
		t.Errorf("detached process state=%q want ready", got)
	}
	if _, still := b.RunningModels()["keep"]; still {
		t.Errorf("a detached model should no longer be reported as running")
	}
	if _, still := b.RunningModels()["drop"]; !still {
		t.Errorf("an untouched model should stay in the table")
	}
	if got := keep.stopCalls.Load(); got != 0 {
		t.Errorf("detaching must not stop the process: stopCalls=%d want 0", got)
	}

	// Shutdown stops what is left and must leave the detached child alone: it is
	// still serving the router that adopted it, and it watches this router's
	// procCtx, which Shutdown must therefore not cancel.
	if err := b.Shutdown(2 * time.Second); err != nil {
		t.Fatalf("Shutdown: %v", err)
	}
	if got := keep.stopCalls.Load(); got != 0 {
		t.Errorf("shutdown stopped a carried-over process: stopCalls=%d want 0", got)
	}
	if got := drop.stopCalls.Load(); got != 1 {
		t.Errorf("shutdown should stop the remaining process exactly once: stopCalls=%d want 1", got)
	}
}

// Without a hand-over, shutdown still tears every process down.
func TestBaseRouter_ShutdownStopsEverythingWithoutDetach(t *testing.T) {
	p := newFakeProcess("m")
	p.markReady()

	b := newTestBase(t, map[string]process.Process{"m": p}, &stubPlanner{})

	if err := b.Shutdown(2 * time.Second); err != nil {
		t.Fatalf("Shutdown: %v", err)
	}
	if got := p.stopCalls.Load(); got != 1 {
		t.Errorf("stopCalls=%d want 1", got)
	}
}

// Adopt registers an already-running process in place of the instance this router
// created, so a replacement router serves the warm child rather than starting its
// own. The discarded instance is stopped so its goroutine does not leak.
func TestBaseRouter_AdoptServesAlreadyRunningProcess(t *testing.T) {
	fresh := newFakeProcess("m")
	b := newTestBase(t, map[string]process.Process{"m": fresh}, &stubPlanner{})

	live := newFakeProcess("m")
	live.markReady()
	b.Adopt(map[string]process.Process{"m": live})

	running := b.RunningModels()
	if running["m"] != process.StateReady {
		t.Errorf("adopted model state=%q want ready", running["m"])
	}
	if got := fresh.stopCalls.Load(); got != 1 {
		t.Errorf("the replaced instance should be stopped once: stopCalls=%d want 1", got)
	}
	if got := live.stopCalls.Load(); got != 0 {
		t.Errorf("the adopted process must not be stopped: stopCalls=%d want 0", got)
	}
}

// DetachRunning with no models is a no-op rather than a deadlock on the run loop.
func TestBaseRouter_DetachRunningEmptyIsNoop(t *testing.T) {
	p := newFakeProcess("m")
	p.markReady()
	b := newTestBase(t, map[string]process.Process{"m": p}, &stubPlanner{})

	if got := b.DetachRunning(nil); len(got) != 0 {
		t.Errorf("detached=%d entries want 0", len(got))
	}
	if _, still := b.RunningModels()["m"]; !still {
		t.Errorf("an empty detach should leave the table untouched")
	}
}
