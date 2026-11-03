package server

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"regexp"
	"strings"
	"sync"
	"time"

	"github.com/Wizard815/Argos/internal/logmon"
	"github.com/Wizard815/Argos/internal/process"
)

// The prompt cache across a restart. A model restarted for new settings (a
// profile edit) loses every slot's KV cache, and each chat it had then reads
// its whole conversation again: 100k tokens is minutes. A model whose command
// has --slot-save-path has each slot saved to a file there before the restart
// (llama-server's POST /slots/{id}?action=save) and loaded back when it is up
// again, before its first request (action=restore). Only when nothing that
// shapes the cache changed: the same model file, context, cache types, RoPE,
// --parallel and KV unified. A cache built with other RoPE settings loads
// without complaint and answers wrong.

type savedSlot struct {
	slot int
	file string
	sig  string
	at   time.Time
}

var slotCache = struct {
	sync.Mutex
	pending map[string][]savedSlot // model id -> slots saved for its next start
	log     *logmon.Monitor
}{pending: map[string][]savedSlot{}}

// slotHTTP talks to llama-server; a save of a long context writes hundreds of MB.
var slotHTTP = &http.Client{Timeout: 3 * time.Minute}

// cacheFlags are the flags whose values must match for a saved cache to fit.
var cacheFlags = map[string]string{
	"-m": "model", "--model": "model", "-c": "ctx", "--ctx-size": "ctx",
	"-ctk": "ctk", "--cache-type-k": "ctk", "-ctv": "ctv", "--cache-type-v": "ctv",
	"--rope-scaling": "rope", "--rope-scale": "rope-scale", "--rope-freq-base": "rope-base", "--rope-freq-scale": "rope-fs",
	"--yarn-orig-ctx": "yarn", "--yarn-ext-factor": "yarn-ext", "--yarn-attn-factor": "yarn-attn",
	"-np": "np", "--parallel": "np", "-fa": "fa", "--flash-attn": "fa",
}

var cacheSwitches = map[string]string{"-kvu": "kvu", "--kv-unified": "kvu", "--no-kv-unified": "no-kvu", "--swa-full": "swa-full"}

// cacheSignature is what of a command decides the shape and meaning of its KV
// cache. Two commands with the same signature can share a saved cache.
func cacheSignature(cmd string) string {
	words := strings.Fields(strings.ReplaceAll(cmd, "\\\n", " "))
	got := map[string]string{}
	for i := 0; i < len(words); i++ {
		w := strings.Trim(words[i], `"'`)
		name, val, eq := strings.Cut(w, "=")
		if k, ok := cacheSwitches[name]; ok {
			got[k] = "on"
			continue
		}
		if k, ok := cacheFlags[name]; ok {
			if !eq && i+1 < len(words) {
				val = strings.Trim(words[i+1], `"'`)
				i++
			}
			got[k] = val
		}
	}
	var b strings.Builder
	for _, k := range []string{"model", "ctx", "ctk", "ctv", "rope", "rope-scale", "rope-base", "rope-fs", "yarn", "yarn-ext", "yarn-attn", "np", "fa", "kvu", "no-kvu", "swa-full"} {
		fmt.Fprintf(&b, "%s=%s;", k, got[k])
	}
	return b.String()
}

// savesSlots reports whether this command lets llama-server save its slots.
func savesSlots(cmd string) bool { return strings.Contains(cmd, "--slot-save-path") }

var unsafeName = regexp.MustCompile(`[^A-Za-z0-9._-]+`)

func slotFile(model string, slot int) string {
	name := unsafeName.ReplaceAllString(model, "_")
	if len(name) > 120 {
		name = name[:120]
	}
	return fmt.Sprintf("argos-%s-slot%d.bin", name, slot)
}

// saveSlots saves every slot of a running model before it is stopped for new
// settings, and remembers them for its next start. Errors are logged, never
// fatal: the restart goes ahead without the cache.
func saveSlots(model, proxy, cmd string, log *logmon.Monitor) {
	if !savesSlots(cmd) || proxy == "" {
		return
	}
	slots, err := listSlots(proxy)
	if err != nil {
		log.Warnf("slot cache: %s: could not list its slots: %v", model, err)
		return
	}
	sig := cacheSignature(cmd)
	var saved []savedSlot
	for _, id := range slots {
		file := slotFile(model, id)
		t0 := time.Now()
		if err := slotAction(proxy, id, "save", file); err != nil {
			log.Warnf("slot cache: %s slot %d: save failed: %v", model, id, err)
			continue
		}
		saved = append(saved, savedSlot{slot: id, file: file, sig: sig, at: time.Now()})
		log.Infof("slot cache: %s slot %d saved to %s in %s", model, id, file, time.Since(t0).Round(time.Millisecond))
	}
	if len(saved) > 0 {
		slotCache.Lock()
		slotCache.pending[model] = saved
		slotCache.log = log
		slotCache.Unlock()
	}
}

// restoreSlots is the process ready hook: a model with slots saved for it gets
// them back before its first request, when its cache would fit.
func restoreSlots(model, proxy, cmd string) {
	slotCache.Lock()
	saved, ok := slotCache.pending[model]
	delete(slotCache.pending, model)
	log := slotCache.log
	slotCache.Unlock()
	if !ok || log == nil {
		return
	}
	if !savesSlots(cmd) {
		log.Infof("slot cache: %s: its new command has no --slot-save-path, so the saved cache is not loaded", model)
		return
	}
	if sig := cacheSignature(cmd); sig != saved[0].sig {
		log.Infof("slot cache: %s: the new settings change the cache (model, context, cache type, RoPE, --parallel or KV unified), so it starts empty", model)
		return
	}
	for _, s := range saved {
		t0 := time.Now()
		if err := slotAction(proxy, s.slot, "restore", s.file); err != nil {
			log.Warnf("slot cache: %s slot %d: restore failed: %v", model, s.slot, err)
			continue
		}
		log.Infof("slot cache: %s slot %d restored from %s in %s", model, s.slot, s.file, time.Since(t0).Round(time.Millisecond))
	}
}

func init() { process.SetReadyHook(restoreSlots) }

// listSlots asks llama-server for its slot ids (GET /slots).
func listSlots(proxy string) ([]int, error) {
	resp, err := slotHTTP.Get(strings.TrimRight(proxy, "/") + "/slots")
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		b, _ := io.ReadAll(io.LimitReader(resp.Body, 300))
		return nil, fmt.Errorf("GET /slots: %s %s", resp.Status, strings.TrimSpace(string(b)))
	}
	var slots []struct {
		ID int `json:"id"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&slots); err != nil {
		return nil, fmt.Errorf("GET /slots: %w", err)
	}
	ids := make([]int, 0, len(slots))
	for _, s := range slots {
		ids = append(ids, s.ID)
	}
	return ids, nil
}

// slotAction is POST /slots/{id}?action=save|restore {"filename": file}.
func slotAction(proxy string, slot int, action, file string) error {
	body, _ := json.Marshal(map[string]string{"filename": file})
	url := fmt.Sprintf("%s/slots/%d?action=%s", strings.TrimRight(proxy, "/"), slot, action)
	resp, err := slotHTTP.Post(url, "application/json", bytes.NewReader(body))
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		b, _ := io.ReadAll(io.LimitReader(resp.Body, 300))
		return fmt.Errorf("%s %s", resp.Status, strings.TrimSpace(string(b)))
	}
	return nil
}

// SaveSlotsBeforeRestart saves the slots of each running model that the next
// config restarts (its settings changed), when its command allows it.
func (s *Server) SaveSlotsBeforeRestart(next map[string]bool) {
	for id := range s.RunningModels() {
		if !next[id] {
			continue
		}
		mc, ok := s.cfg.Models[id]
		if !ok {
			continue
		}
		saveSlots(id, mc.Proxy, mc.Cmd, s.proxylog)
	}
}
