package router

import (
	"bytes"
	"io"
	"regexp"
	"strconv"
	"sync"
	"time"
)

// What a model is doing with a request, read from its llama-server log: the
// scheduler only knows a request is a prefill when the client declares it
// (X-Argos-Intent), and most clients do not. llama-server reports its prompt
// processing as it goes:
//
//	slot launch_slot_: id  0 | task 0 | processing task, is_child = 0
//	slot print_timing: id  0 | task 0 | prompt processing, n_tokens =   4096, progress = 0.21, t =   6.02 s / 680.79 tokens per second
//	slot update_slots: id  0 | task 0 | prompt done, n_tokens = 19358, batch.n_tokens = 1
//	slot      release: id  0 | task 0 | stop processing: n_tokens = 19400, truncated = 0
//
// so a model is prefilling from "processing task" until "prompt done" (or the
// prompt eval timing, or the slot's release), with progress as it reports it.
var (
	rePrefillStart    = regexp.MustCompile(`processing task`)
	rePrefillProgress = regexp.MustCompile(`prompt processing, n_tokens\s*=\s*(\d+), progress\s*=\s*([0-9.]+)(?:, t\s*=\s*[0-9.]+ s / ([0-9.]+) tokens per second)?`)
	rePrefillDone     = regexp.MustCompile(`prompt (?:processing )?done|prompt eval time|stop processing`)
)

// prefillStale drops a prefill nobody has heard from: a model that crashed or
// was killed mid-prompt never logs its end.
const prefillStale = 2 * time.Minute

// Prefill is one model's prompt processing in progress.
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

// PrefillWatch holds what each model is prefilling. It is shared by every
// router so a model carried across a hot reload keeps being watched: its
// process keeps writing to the log writer it started with.
type PrefillWatch struct {
	mu      sync.Mutex
	now     func() time.Time
	models  map[string]*Prefill
	partial map[string][]byte // a line split across writes
}

// Prefills is the engine's watch.
var Prefills = NewPrefillWatch(time.Now)

func NewPrefillWatch(now func() time.Time) *PrefillWatch {
	return &PrefillWatch{now: now, models: map[string]*Prefill{}, partial: map[string][]byte{}}
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
	now := p.now()
	if m := rePrefillProgress.FindSubmatch(line); m != nil {
		st := p.models[model]
		if st == nil {
			st = &Prefill{Started: now}
			p.models[model] = st
		}
		st.Tokens, _ = strconv.Atoi(string(m[1]))
		st.Progress, _ = strconv.ParseFloat(string(m[2]), 64)
		if len(m[3]) > 0 {
			st.TokensPerSecond, _ = strconv.ParseFloat(string(m[3]), 64)
		}
		if st.Progress > 0 {
			st.Total = int(float64(st.Tokens)/st.Progress + 0.5)
		}
		st.Updated = now
		if st.Progress >= 1 {
			delete(p.models, model)
		}
		return
	}
	if rePrefillDone.Match(line) {
		delete(p.models, model)
		return
	}
	if rePrefillStart.Match(line) {
		p.models[model] = &Prefill{Started: now, Updated: now}
	}
}

// Get reports the model's prefill in progress, if any.
func (p *PrefillWatch) Get(model string) (Prefill, bool) {
	p.mu.Lock()
	defer p.mu.Unlock()
	st, ok := p.models[model]
	if !ok {
		return Prefill{}, false
	}
	if p.now().Sub(st.Updated) > prefillStale {
		delete(p.models, model)
		return Prefill{}, false
	}
	return *st, true
}
