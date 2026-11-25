package server

import (
	"bytes"
	"encoding/json"
	"hash/fnv"
	"io"
	"net/http"
	"sort"
	"strconv"
	"strings"
	"sync"

	"github.com/Wizard815/Argos/internal/chain"
	"github.com/Wizard815/Argos/internal/router"
	"github.com/Wizard815/Argos/internal/swaputil"
	"github.com/tidwall/gjson"
	"github.com/tidwall/sjson"
)

// Conversation affinity: a conversation goes back where its earlier turns are
// cached. llama-server keeps each slot's prompt in its KV cache, so the next
// turn of a chat only reads its new part, but only in the slot (and the copy,
// for a model run once per GPU) that read the earlier turns. Sent anywhere
// else, it reads the whole conversation again: tens of seconds at long
// contexts, while every other chat on that model slows down.
//
//   - a model run as copies (multi_model, a spillover selector): the copy a
//     conversation used last, while it has room (selector.go);
//   - a model with --parallel N: the slot, as llama-server's id_slot, while
//     that slot is free. A busy slot is never asked for: llama-server would
//     hold the request until that slot is done, though another may be free.
//
// A conversation is known by its start: the messages up to and including the
// first user message (the system prompt and the task), which stay the same
// while the conversation grows.

const affinityKeep = 4096 // conversations remembered per model or selector

type affinityMap struct {
	vals map[uint64]affinityEntry
	seq  uint64
}

type affinityEntry struct {
	val  string
	used uint64
}

func (m *affinityMap) get(k uint64) (string, bool) {
	e, ok := m.vals[k]
	return e.val, ok
}

func (m *affinityMap) put(k uint64, v string) {
	if m.vals == nil {
		m.vals = map[uint64]affinityEntry{}
	}
	m.seq++
	m.vals[k] = affinityEntry{val: v, used: m.seq}
	if len(m.vals) > affinityKeep { // forget the least recently used quarter
		cut := m.seq - uint64(affinityKeep*3/4)
		for key, e := range m.vals {
			if e.used < cut {
				delete(m.vals, key)
			}
		}
	}
}

type affinityState struct {
	mu     sync.Mutex
	copies map[string]*affinityMap // selector -> conversation -> copy's model id
	slots  map[string]*affinityMap // model -> conversation -> slot number
	// pinned counts requests sent to each slot that the log may not show yet
	pinned map[string]map[int]int
	// lastUse orders a model's slots by when they were last given out
	lastUse map[string]map[int]uint64
	tick    uint64
	// busySlots lists a model's slots the log shows working (router.Prefills)
	busySlots func(model string) []router.Slot
}

func newAffinity() *affinityState {
	return &affinityState{copies: map[string]*affinityMap{}, slots: map[string]*affinityMap{},
		pinned: map[string]map[int]int{}, lastUse: map[string]map[int]uint64{}, busySlots: router.Prefills.Slots}
}

// conversationKey identifies a conversation by its start, or 0 when the
// request has none to go by.
func conversationKey(body []byte) uint64 {
	h := fnv.New64a()
	msgs := gjson.GetBytes(body, "messages")
	if !msgs.IsArray() {
		msgs = gjson.GetBytes(body, "input") // the Responses API
	}
	if msgs.IsArray() {
		n := 0
		msgs.ForEach(func(_, m gjson.Result) bool {
			h.Write([]byte(m.Get("role").String()))
			h.Write([]byte{0})
			h.Write([]byte(m.Get("content").Raw))
			n++
			return m.Get("role").String() != "user"
		})
		if n == 0 {
			return 0
		}
		return h.Sum64()
	}
	p := gjson.GetBytes(body, "prompt").String()
	if p == "" {
		return 0
	}
	if len(p) > 1024 {
		p = p[:1024]
	}
	h.Write([]byte(p))
	return h.Sum64()
}

// copyFor is the copy a conversation used last for a selector, or "".
func (a *affinityState) copyFor(selector string, key uint64) string {
	if key == 0 {
		return ""
	}
	a.mu.Lock()
	defer a.mu.Unlock()
	if m := a.copies[selector]; m != nil {
		v, _ := m.get(key)
		return v
	}
	return ""
}

func (a *affinityState) rememberCopy(selector string, key uint64, modelID string) {
	if key == 0 {
		return
	}
	a.mu.Lock()
	defer a.mu.Unlock()
	if a.copies[selector] == nil {
		a.copies[selector] = &affinityMap{}
	}
	a.copies[selector].put(key, modelID)
}

// pickSlot is the slot to send a conversation to on a model with n slots, or
// -1 to let llama-server choose (every slot is busy). release must be called
// when the request is done.
func (a *affinityState) pickSlot(model string, n int, key uint64) (slot int, release func()) {
	busy := map[int]bool{}
	for _, s := range a.busySlots(model) {
		busy[s.ID] = true
	}
	a.mu.Lock()
	defer a.mu.Unlock()
	for id, c := range a.pinned[model] {
		if c > 0 {
			busy[id] = true
		}
	}
	free := func(id int) bool { return id >= 0 && id < n && !busy[id] }
	slot = -1
	if m := a.slots[model]; m != nil && key != 0 {
		if v, ok := m.get(key); ok {
			if id, err := strconv.Atoi(v); err == nil && free(id) {
				slot = id
			}
		}
	}
	if slot < 0 {
		// a slot of its own: the free one given out longest ago, so the
		// conversations' caches overwrite each other as little as possible
		for id := 0; id < n; id++ {
			if free(id) && (slot < 0 || a.lastUse[model][id] < a.lastUse[model][slot]) {
				slot = id
			}
		}
	}
	if slot < 0 {
		return -1, func() {}
	}
	if a.slots[model] == nil {
		a.slots[model] = &affinityMap{}
	}
	if key != 0 {
		a.slots[model].put(key, strconv.Itoa(slot))
	}
	if a.pinned[model] == nil {
		a.pinned[model] = map[int]int{}
		a.lastUse[model] = map[int]uint64{}
	}
	a.pinned[model][slot]++
	a.tick++
	a.lastUse[model][slot] = a.tick
	var once sync.Once
	return slot, func() {
		once.Do(func() {
			a.mu.Lock()
			a.pinned[model][slot]--
			a.mu.Unlock()
		})
	}
}

// slotPaths take llama-server's id_slot.
var slotPaths = map[string]bool{"/v1/chat/completions": true, "/v1/completions": true, "/completion": true, "/completions": true}

// llamaServer reports whether a command runs llama-server (here, in a
// container next to Argos, or over SSH): only it knows id_slot.
func llamaServer(cmd string) bool {
	return strings.Contains(cmd, "llama-server") || strings.Contains(cmd, "remote-llama") || strings.Contains(cmd, "docker-llama")
}

// CreateSlotAffinityMiddleware sends each conversation to the slot that has
// its earlier turns cached (id_slot), on models serving several chats at once.
func CreateSlotAffinityMiddleware(s *Server) chain.Middleware {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			if r.Method != http.MethodPost || !slotPaths[strings.TrimSuffix(r.URL.Path, "/")] || s.affinity == nil {
				next.ServeHTTP(w, r)
				return
			}
			model, _ := swaputil.ExtractModel(r)
			id, ok := s.cfg.RealModelName(model)
			if !ok {
				next.ServeHTTP(w, r)
				return
			}
			mc := s.cfg.Models[id]
			n := parallelFromCmd(mc.Cmd)
			if n < 2 || !llamaServer(mc.Cmd) {
				next.ServeHTTP(w, r)
				return
			}
			body, err := io.ReadAll(r.Body)
			r.Body = io.NopCloser(bytes.NewReader(body))
			if err != nil || gjson.GetBytes(body, "id_slot").Exists() { // the client chose itself
				next.ServeHTTP(w, r)
				return
			}
			slot, release := s.affinity.pickSlot(id, n, conversationKey(body))
			defer release()
			if slot >= 0 {
				if b, err := sjson.SetBytes(body, "id_slot", slot); err == nil {
					body = b
				}
			}
			r.Body = io.NopCloser(bytes.NewReader(body))
			r.ContentLength = int64(len(body))
			r.Header.Set("Content-Length", strconv.Itoa(len(body)))
			next.ServeHTTP(w, r)
		})
	}
}

// handleAPISlotsLive is what every running model's slots are doing right now,
// under any router, for Schedule -> Live:
//
//	GET /api/slots/live -> {"models": [{"model", "parallel", "answer_first",
//	    "prefill_tps", "slots": [router.Slot...]}]}
func (s *Server) handleAPISlotsLive(w http.ResponseWriter, r *http.Request) {
	type row struct {
		Model       string        `json:"model"`
		Parallel    int           `json:"parallel"`
		AnswerFirst bool          `json:"answer_first"`
		PrefillTPS  float64       `json:"prefill_tps,omitempty"`
		Slots       []router.Slot `json:"slots"`
	}
	af := s.cfg.Routing.Router.Settings.Pool.AnswerFirst
	out := []row{}
	for id := range s.RunningModels() {
		mc := s.cfg.Models[id]
		rw := row{Model: id, Parallel: parallelFromCmd(mc.Cmd), Slots: router.Prefills.Slots(id),
			AnswerFirst: af.Applies(id) || s.answerFirstAuto(id)}
		if rw.Slots == nil {
			rw.Slots = []router.Slot{}
		}
		if sp, ok := prefillSpeed(id); ok {
			rw.PrefillTPS = sp
		}
		out = append(out, rw)
	}
	sort.Slice(out, func(i, j int) bool { return out[i].Model < out[j].Model })
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]any{"models": out})
}
