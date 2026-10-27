package router

import (
	"context"
	"sync"
	"time"

	"github.com/Wizard815/Argos/internal/gpumem"
	"github.com/Wizard815/Argos/internal/process"
)

// Another app on the GPUs (a game, ComfyUI, a llama-server Argos did not
// start) holds VRAM the pool never planned, so the pool used to place models
// as if that memory were free and the Schedule showed the cards empty. This
// reads what each card really has in use and takes away what Argos's own
// running models account for: the rest is held by something else, and the
// pool plans around it.
//
// The reading lags a load or an unload by a few seconds: a model still
// unloading would read as someone else's for a moment. So the smallest of
// the last few readings is used, which a few seconds of leftovers cannot
// raise.

const (
	outsideEvery   = 5 * time.Second
	outsideSamples = 4 // the smallest of these readings counts
)

// readCardUsed reads live VRAM in use per card. A var for tests.
var readCardUsed = cardUsed

// cardUsedFrom is a reading the engine already takes (its performance
// monitor, which can read LACT where rocm-smi and sysfs are not available);
// set by SetCardUsedSource.
var (
	cardUsedMu   sync.RWMutex
	cardUsedFrom func() map[string]float64
)

// SetCardUsedSource gives the pool a live VRAM reading per card (GiB, keyed
// by card id), tried before rocm-smi and sysfs.
func SetCardUsedSource(f func() map[string]float64) {
	cardUsedMu.Lock()
	cardUsedFrom = f
	cardUsedMu.Unlock()
}

func cardUsed() map[string]float64 {
	cardUsedMu.RLock()
	f := cardUsedFrom
	cardUsedMu.RUnlock()
	if f != nil {
		if used := f(); len(used) > 0 {
			return used
		}
	}
	return gpumem.Used()
}

// lastOutside carries the last reading over a config reload, so a new pool
// does not plan with the other apps' memory forgotten until it reads again.
var (
	lastOutsideMu sync.Mutex
	lastOutside   = map[string]float64{}
)

// sampleOutside records one reading of live VRAM per card (GiB).
func (p *poolSwapper) sampleOutside(live map[string]float64, running map[string]process.ProcessState) {
	p.mu.Lock()
	defer p.mu.Unlock()
	if len(p.cards) == 0 || len(live) == 0 {
		return
	}
	for _, c := range p.cards {
		used, ok := live[c.id]
		if !ok {
			continue
		}
		planned := 0.0
		for id := range running {
			planned += p.usage(id, c)
		}
		h := append(p.outsideHist[c.id], used-planned)
		if len(h) > outsideSamples {
			h = h[len(h)-outsideSamples:]
		}
		p.outsideHist[c.id] = h
		low := h[0]
		for _, v := range h[1:] {
			low = min(low, v)
		}
		p.outside[c.id] = max(low, 0)
	}
	lastOutsideMu.Lock()
	for k, v := range p.outside {
		lastOutside[k] = v
	}
	lastOutsideMu.Unlock()
}

// outsideOn is the VRAM on card c held by something other than Argos's
// running models, beyond the reserve set aside for exactly that (the driver,
// a desktop). Called with p.mu held.
func (p *poolSwapper) outsideOn(c poolCard) float64 {
	return max(p.outside[c.id]-p.reserveShare, 0)
}

// watchOutside reads the cards every outsideEvery until ctx ends.
func (r *Pool) watchOutside(ctx context.Context) {
	if len(r.swapper.cards) == 0 {
		return
	}
	t := time.NewTicker(outsideEvery)
	defer t.Stop()
	misses := 0
	for {
		if live := readCardUsed(); len(live) > 0 {
			r.swapper.sampleOutside(live, r.RunningModels())
		} else if misses++; misses == 3 { // the performance monitor may not have read yet
			r.swapper.logger.Warnf("pool: cannot read the VRAM in use on each card (no performance reading, rocm-smi or sysfs here), so memory other apps hold on the GPUs is not planned around")
		}
		select {
		case <-ctx.Done():
			return
		case <-t.C:
		}
	}
}
