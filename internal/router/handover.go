package router

import (
	"time"

	"github.com/mostlygeek/llama-swap/internal/process"
)

// handoverReq carries a reload's process hand-over into the run loop, which owns
// the process table: detaching and adopting both mutate that table, so they
// cannot be done from the reload goroutine.
type handoverReq struct {
	// detach names models to remove from the table without stopping them.
	detach []string
	// adopt registers already-running processes, replacing same-ID instances
	// this router created but never served traffic with.
	adopt map[string]process.Process
	// detached receives the processes removed by detach. Always non-nil: the
	// caller uses it to wait for the run loop to apply the request.
	detached chan<- map[string]process.Process
}

// DetachRunning removes models from the process table without stopping them and
// returns the live processes so a replacement router can adopt them. A hot
// reload uses it to carry over children whose configuration did not change.
func (b *baseRouter) DetachRunning(models []string) map[string]process.Process {
	if len(models) == 0 {
		return map[string]process.Process{}
	}
	respond := make(chan map[string]process.Process, 1)
	select {
	case b.handoverCh <- handoverReq{detach: models, detached: respond}:
	case <-b.runDone:
		return map[string]process.Process{}
	}
	select {
	case detached := <-respond:
		return detached
	case <-b.runDone:
		return map[string]process.Process{}
	}
}

// Adopt registers processes that are already running so this router serves them
// in place of the instances it created for the same IDs. Call it before the
// router serves traffic.
func (b *baseRouter) Adopt(models map[string]process.Process) {
	if len(models) == 0 {
		return
	}
	respond := make(chan map[string]process.Process, 1)
	select {
	case b.handoverCh <- handoverReq{adopt: models, detached: respond}:
	case <-b.runDone:
		return
	}
	select {
	case <-respond:
	case <-b.runDone:
	}
}

// stopReplaced stops an instance this router created but never served traffic
// with, so adopting a live process for the same ID does not leak its goroutine.
func stopReplaced(existing, adopted process.Process) {
	if existing == nil || existing == adopted {
		return
	}
	_ = existing.Stop(5 * time.Second)
}
