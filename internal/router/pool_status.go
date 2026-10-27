package router

import (
	"sort"
	"time"

	"github.com/Wizard815/Argos/internal/process"
	"github.com/Wizard815/Argos/internal/router/scheduler"
)

// PoolReporter is implemented by routers that can describe their pool for the
// status API.
type PoolReporter interface {
	PoolStatus() PoolStatus
}

// PoolStatus is a point-in-time view of the pool: what each card holds, where
// each model runs, and what is queued or loading.
type PoolStatus struct {
	Router         string            `json:"router"`
	Enabled        bool              `json:"enabled"`
	Placement      bool              `json:"placement"` // per-card sizes are known and models are placed
	BudgetGiB      float64           `json:"budget_gib"`
	ProtectSeconds float64           `json:"protect_seconds"`
	Cards          []PoolCardStatus  `json:"cards"`
	Models         []PoolModelStatus `json:"models"`
	Queue          []PoolQueueStatus `json:"queue"`
	Swaps          []PoolSwapStatus  `json:"swaps"`
}

// PoolCardStatus is one GPU. UsedGiB counts the running models' planned share,
// not live VRAM, so it is the number the pool itself plans with.
type PoolCardStatus struct {
	ID      string   `json:"id"`
	CapGiB  float64  `json:"cap_gib"`
	UsedGiB float64  `json:"used_gib"`
	FreeGiB float64  `json:"free_gib"`
	Models  []string `json:"models"`
}

// PoolModelStatus is one configured model.
type PoolModelStatus struct {
	ID          string     `json:"id"`
	State       string     `json:"state"`
	SizeGiB     float64    `json:"size_gib"`
	SizeKnown   bool       `json:"size_known"`
	Cards       []string   `json:"cards"`
	InFlight    int        `json:"in_flight"`
	Persistent  bool       `json:"persistent"`
	Protected   bool       `json:"protected"` // used within protectSeconds
	LastUsed    *time.Time `json:"last_used,omitempty"`
	LoadSeconds float64    `json:"load_seconds,omitempty"` // measured; 0 until loaded once
	// Prefill is the prompt the model is processing, read from its log; nil
	// when it is not prefilling (generating, idle or stopped).
	Prefill *Prefill `json:"prefill,omitempty"`
	// Slots is every llama-server slot busy with a request, oldest first:
	// reading its prompt or generating. Two chats on one model are two slots.
	Slots []Slot `json:"slots,omitempty"`
	// Crash is the model's last crash in the last ten minutes: the line it died
	// on, and whether it crashed often enough that requests no longer start it.
	Crash *Crash `json:"crash,omitempty"`
	// AnswerFirst: a long new prompt waits while another chat on this model
	// is answering (routing.router.settings.pool.answerFirst).
	AnswerFirst bool `json:"answer_first,omitempty"`
	// OneAtATime: the model gets one request at a time, the rest wait in
	// Argos (routing.router.settings.pool.oneAtATime).
	OneAtATime bool `json:"one_at_a_time,omitempty"`
	// UpdatePending: its settings changed while it was busy; it restarts with
	// the new ones once its requests are done. Since when it waits.
	UpdatePending *time.Time `json:"update_pending,omitempty"`
	// SoloTokensPerSecond is its answer speed with nothing else busy on its
	// cards, and Slowdowns how much it slows beside each other model, both
	// learned from the logs.
	SoloTokensPerSecond float64    `json:"solo_tps,omitempty"`
	Slowdowns           []Slowdown `json:"slowdowns,omitempty"`
}

// PoolQueueStatus is one request waiting for room or for a busy model.
type PoolQueueStatus struct {
	Model         string  `json:"model"`
	Position      int     `json:"position"`
	WaitedSeconds float64 `json:"waited_seconds"`
}

// PoolSwapStatus is one model loading.
type PoolSwapStatus struct {
	Model          string   `json:"model"`
	Evict          []string `json:"evict"`
	Waiters        int      `json:"waiters"`
	ElapsedSeconds float64  `json:"elapsed_seconds"`
}

// PoolStatus implements PoolReporter.
func (r *Pool) PoolStatus() PoolStatus {
	sched, _ := r.SchedulerStatus()
	return r.swapper.status(r.RunningModels(), sched)
}

// status builds the snapshot from the swapper's own state plus the live
// process states and the scheduler's queue.
func (p *poolSwapper) status(running map[string]process.ProcessState, sched scheduler.Status) PoolStatus {
	p.mu.RLock()
	defer p.mu.RUnlock()

	now := p.now()
	out := PoolStatus{
		Router:         "pool",
		Enabled:        true,
		Placement:      len(p.cards) > 0,
		BudgetGiB:      p.budget,
		ProtectSeconds: p.protect.Seconds(),
		Cards:          []PoolCardStatus{},
		Models:         []PoolModelStatus{},
		Queue:          []PoolQueueStatus{},
		Swaps:          []PoolSwapStatus{},
	}

	ids := make([]string, 0, len(p.sizes))
	for id := range p.sizes {
		ids = append(ids, id)
	}
	sort.Strings(ids)

	for _, id := range ids {
		m := PoolModelStatus{
			ID:         id,
			State:      string(process.StateStopped),
			SizeGiB:    p.sizes[id],
			SizeKnown:  p.sizes[id] > 0,
			Cards:      []string{},
			InFlight:   sched.InFlight[id],
			Persistent: p.persistent[id],
		}
		if st, ok := running[id]; ok {
			m.State = string(st)
			m.Cards = append(m.Cards, p.placed[id]...)
			if pf, ok := Prefills.Get(id); ok {
				m.Prefill = &pf
			}
			m.Slots = Prefills.Slots(id)
		}
		if cr, ok := Crashes.Get(id); ok {
			m.Crash = &cr
		}
		if v, ok := Sharing.Solo(id); ok {
			m.SoloTokensPerSecond = v
		}
		m.Slowdowns = Sharing.Slowdowns(id)
		if t, ok := p.lastUsed[id]; ok {
			t := t
			m.LastUsed = &t
			m.Protected = now.Sub(t) < p.protect
		}
		if d, ok := p.loadTime[id]; ok {
			m.LoadSeconds = d.Seconds()
		}
		out.Models = append(out.Models, m)
	}

	for _, c := range p.cards {
		cs := PoolCardStatus{ID: c.id, CapGiB: c.cap, Models: []string{}}
		for _, id := range ids {
			if _, ok := running[id]; !ok {
				continue
			}
			if u := p.usage(id, c); u > 0 {
				cs.UsedGiB += u
				cs.Models = append(cs.Models, id)
			}
		}
		cs.FreeGiB = max(c.cap-cs.UsedGiB, 0)
		out.Cards = append(out.Cards, cs)
	}

	for _, q := range sched.Queued {
		out.Queue = append(out.Queue, PoolQueueStatus{Model: q.Model, Position: q.Position, WaitedSeconds: q.Waited.Seconds()})
	}
	for _, s := range sched.Swaps {
		evict := s.Evict
		if evict == nil {
			evict = []string{}
		}
		out.Swaps = append(out.Swaps, PoolSwapStatus{Model: s.Model, Evict: evict, Waiters: s.Waiters, ElapsedSeconds: s.Elapsed.Seconds()})
	}
	return out
}
