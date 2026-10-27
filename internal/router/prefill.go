package router

import (
	"bytes"
	"io"
	"regexp"
	"sort"
	"strconv"
	"sync"
	"time"
)

// What a model is doing with each request, read from its llama-server log: the
// scheduler only knows a request is a prefill when the client declares it
// (X-Argos-Intent), and most clients do not. llama-server serves requests in
// slots (-np) and tags every line with the slot and the task it is working on:
//
//	slot launch_slot_: id  1 | task 0 | processing task, is_child = 0
//	slot print_timing: id  1 | task 0 | prompt processing, n_tokens =   4096, progress = 0.21, t =   6.02 s / 680.79 tokens per second
//	slot update_slots: id  1 | task 0 | prompt done, n_tokens = 19358, batch.n_tokens = 1
//	slot print_timing: id  1 | task 0 | n_gen =    132, tg =  43.21 t/s, tg_3s =  43.53 t/s
//	slot      release: id  1 | task 0 | stop processing: n_tokens = 19400, truncated = 0
//
// so each slot is reading its prompt (prefill) from "processing task" until the
// prompt is done (progress 1, "prompt done", or the first n_gen line), then
// generating until it is released. Two chats on one model are two slots.
var (
	reSlot            = regexp.MustCompile(`\bid\s+(\d+)\s*\|\s*task\s+(-?\d+)`)
	reSlotStart       = regexp.MustCompile(`processing task`)
	rePrefillProgress = regexp.MustCompile(`prompt processing, n_tokens\s*=\s*(\d+), progress\s*=\s*([0-9.]+)(?:, t\s*=\s*[0-9.]+ s / ([0-9.]+) tokens per second)?`)
	rePromptDone      = regexp.MustCompile(`prompt (?:processing )?done`)
	reGenerating      = regexp.MustCompile(`n_gen\s*=\s*(\d+), tg\s*=\s*([0-9.]+) t/s(?:, tg_3s\s*=\s*([0-9.]+) t/s)?`)
	reSlotEnd         = regexp.MustCompile(`stop processing|release`)
)

// prefillStale drops a slot nobody has heard from: a model that crashed or was
// killed mid-request never logs its release.
const prefillStale = 2 * time.Minute

const (
	PhasePrefill    = "prefill"
	PhaseGenerating = "generating"
)

// Prefill is one slot's prompt processing in progress. It is also what a
// model reports as its prefill: its oldest slot still reading a prompt.
type Prefill struct {
	// Tokens is how many prompt tokens are processed so far; 0 until the first
	// progress report (llama-server reports every batch of n_batch tokens).
	Tokens int `json:"tokens"`
	// Total is the prompt's length, worked out from Tokens and Progress; 0
	// until the first progress report.
	Total int `json:"total"`
	// Progress is 0..1, as llama-server reports it.
	Progress float64 `json:"progress"`
	// TokensPerSecond is the prompt processing speed so far.
	TokensPerSecond float64   `json:"tokens_per_second"`
	Started         time.Time `json:"started"`
	Updated         time.Time `json:"updated"`
}

// Slot is one llama-server slot busy with a request: reading its prompt, then
// generating the answer.
type Slot struct {
	ID    int    `json:"id"`
	Task  int    `json:"task"`
	Phase string `json:"phase"`
	Prefill
	// Generated is how many tokens of the answer are out so far, and
	// GenTokensPerSecond the speed over the last few seconds.
	Generated          int     `json:"generated,omitempty"`
	GenTokensPerSecond float64 `json:"gen_tokens_per_second,omitempty"`
}

// PrefillWatch holds what each model's slots are doing. It is shared by every
// router so a model carried across a hot reload keeps being watched: its
// process keeps writing to the log writer it started with.
type PrefillWatch struct {
	mu      sync.Mutex
	now     func() time.Time
	models  map[string]map[int]*Slot
	partial map[string][]byte // a line split across writes
	// onLine also sees every line (the crash watch's fatal lines)
	onLine func(model string, line []byte)
}

// Prefills is the engine's watch.
var Prefills = NewPrefillWatch(time.Now)

func NewPrefillWatch(now func() time.Time) *PrefillWatch {
	return &PrefillWatch{now: now, models: map[string]map[int]*Slot{}, partial: map[string][]byte{}}
}

// Tap returns a writer for a model's log that feeds the watch and passes
// everything on to w.
func (p *PrefillWatch) Tap(model string, w io.Writer) io.Writer {
	return &prefillTap{watch: p, model: model, w: w}
}

type prefillTap struct {
	watch *PrefillWatch
	model string
	w     io.Writer
}

func (t *prefillTap) Write(b []byte) (int, error) {
	t.watch.Feed(t.model, b)
	return t.w.Write(b)
}

// Feed reads a chunk of a model's log.
func (p *PrefillWatch) Feed(model string, b []byte) {
	p.mu.Lock()
	defer p.mu.Unlock()
	buf := append(p.partial[model], b...)
	for {
		i := bytes.IndexByte(buf, '\n')
		if i < 0 {
			break
		}
		p.line(model, buf[:i])
		if p.onLine != nil {
			p.onLine(model, buf[:i])
		}
		buf = buf[i+1:]
	}
	if len(buf) > 4096 { // not a log line: do not keep growing
		buf = nil
	}
	if len(buf) == 0 {
		delete(p.partial, model)
	} else {
		p.partial[model] = append([]byte(nil), buf...)
	}
}

func (p *PrefillWatch) line(model string, line []byte) {
	m := reSlot.FindSubmatch(line)
	if m == nil {
		return
	}
	id, _ := strconv.Atoi(string(m[1]))
	task, _ := strconv.Atoi(string(m[2]))
	if task < 0 { // "selected slot by LRU" comes before the slot has a task
		return
	}
	now := p.now()
	slots := p.models[model]
	if slots == nil {
		slots = map[int]*Slot{}
		p.models[model] = slots
	}
	st := slots[id]
	if st != nil && st.Task != task { // the slot moved on to another request
		st = nil
	}
	newSlot := func() *Slot {
		s := &Slot{ID: id, Task: task, Phase: PhasePrefill, Prefill: Prefill{Started: now, Updated: now}}
		slots[id] = s
		return s
	}
	switch {
	case reSlotEnd.Match(line):
		delete(slots, id)
	case reSlotStart.Match(line):
		newSlot()
	default:
		if g := reGenerating.FindSubmatch(line); g != nil {
			if st == nil {
				st = newSlot()
			}
			st.Phase = PhaseGenerating
			st.Generated, _ = strconv.Atoi(string(g[1]))
			speed := g[2]
			if len(g[3]) > 0 {
				speed = g[3] // the last few seconds: what it does now
			}
			st.GenTokensPerSecond, _ = strconv.ParseFloat(string(speed), 64)
			st.Updated = now
			return
		}
		if pp := rePrefillProgress.FindSubmatch(line); pp != nil {
			if st == nil {
				st = newSlot()
			}
			st.Tokens, _ = strconv.Atoi(string(pp[1]))
			st.Progress, _ = strconv.ParseFloat(string(pp[2]), 64)
			if len(pp[3]) > 0 {
				st.TokensPerSecond, _ = strconv.ParseFloat(string(pp[3]), 64)
			}
			if st.Progress > 0 {
				st.Total = int(float64(st.Tokens)/st.Progress + 0.5)
			}
			if st.Progress >= 1 {
				st.Phase = PhaseGenerating
			}
			st.Updated = now
			return
		}
		if st != nil && rePromptDone.Match(line) {
			st.Phase = PhaseGenerating
			st.Updated = now
		}
	}
}

// Slots reports the model's busy slots, oldest request first.
func (p *PrefillWatch) Slots(model string) []Slot {
	p.mu.Lock()
	defer p.mu.Unlock()
	slots := p.models[model]
	out := make([]Slot, 0, len(slots))
	for id, st := range slots {
		if p.now().Sub(st.Updated) > prefillStale {
			delete(slots, id)
			continue
		}
		out = append(out, *st)
	}
	sort.Slice(out, func(i, j int) bool {
		if !out[i].Started.Equal(out[j].Started) {
			return out[i].Started.Before(out[j].Started)
		}
		return out[i].ID < out[j].ID
	})
	return out
}

// Get reports the model's prefill in progress, if any: its oldest slot still
// reading a prompt.
func (p *PrefillWatch) Get(model string) (Prefill, bool) {
	for _, s := range p.Slots(model) {
		if s.Phase == PhasePrefill {
			return s.Prefill, true
		}
	}
	return Prefill{}, false
}

// Forget drops what a model's slots were doing: its process stopped, so no
// slot of it is busy any more.
func (p *PrefillWatch) Forget(model string) {
	p.mu.Lock()
	defer p.mu.Unlock()
	delete(p.models, model)
	delete(p.partial, model)
}
