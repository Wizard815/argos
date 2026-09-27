package server

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/mostlygeek/llama-swap/internal/odysseus"
)

// odysseusState is the integration's runtime bookkeeping: when it last ran, what
// it found, and the last error. Serialising refreshes behind one mutex keeps the
// timer, the GET /v1/models trigger and the manual endpoint from stampeding the
// same endpoint and writing the same file concurrently.
type odysseusState struct {
	mu          sync.Mutex
	lastRun     time.Time
	lastErr     string
	lastModels  int
	lastProfile int
	lastWrote   bool

	// choices is the per-model override state (model ID -> Odysseus preset
	// label), loaded at construction and kept current by refreshes. modelStatus
	// reads it to report each model's effective launch configuration.
	choices map[string]string
}

// odysseusMinTriggerInterval coalesces the /v1/models trigger. That endpoint is
// what every OpenAI-compatible client polls, and a refresh is an HTTP call to
// another service plus a possible config write; doing it on every listing would
// be needlessly chatty. The timer and the manual endpoint are unaffected.
const odysseusMinTriggerInterval = 30 * time.Second

// SetConfigPaths records the -config and -config-dir paths so the Odysseus
// integration can enumerate the other config sources when guarding against
// duplicate identity keys. Called once by main after flags are parsed; without
// it, refreshes still work but the guard can only see this fragment.
func (s *Server) SetConfigPaths(configPath, configDir string) {
	s.cfgPath = configPath
	s.cfgDir = configDir
}

// odysseusOptions maps config onto the integration's options.
func (s *Server) odysseusOptions() odysseus.Options {
	o := s.cfg.Odysseus
	if o == nil {
		return odysseus.Options{}
	}
	return odysseus.Options{
		BaseURL:       o.BaseURL,
		Token:         o.Token,
		TokenEnv:      o.TokenEnv,
		TokenFile:     o.TokenFile,
		ProfilePrefix: o.ProfilePrefix,
		Statuses:      o.Statuses,
		StateFile:     o.StateFile,
		ChoicesPath:   o.ChoicesPath,
		EnsureDio:     o.DioEnabled(),
		Timeout:       time.Duration(o.TimeoutSeconds) * time.Second,
	}
}

// odysseusEnabled reports whether the integration is configured and on.
func (s *Server) odysseusEnabled() bool {
	return s.cfg.Odysseus != nil && s.cfg.Odysseus.Enabled && s.cfg.Odysseus.OutputFile != ""
}

// odysseusOtherSources lists every config source except the fragment this
// integration owns, so the duplicate guard sees hand-written models and profiles
// but not its own previous output.
func (s *Server) odysseusOtherSources() []string {
	var out []string
	if s.cfgPath != "" {
		out = append(out, s.cfgPath)
	}
	if s.cfgDir != "" {
		entries, err := os.ReadDir(s.cfgDir)
		if err == nil {
			for _, e := range entries {
				if e.IsDir() {
					continue
				}
				ext := filepath.Ext(e.Name())
				if ext == ".yaml" || ext == ".yml" {
					out = append(out, filepath.Join(s.cfgDir, e.Name()))
				}
			}
		}
	}
	return out
}

// runOdysseusRefresh performs one refresh, serialised against every other
// caller. It returns the result so callers can report it.
func (s *Server) runOdysseusRefresh(ctx context.Context) (odysseus.RefreshResult, error) {
	var rr odysseus.RefreshResult
	if !s.odysseusEnabled() {
		return rr, nil
	}

	s.odysseus.mu.Lock()
	defer s.odysseus.mu.Unlock()

	rr, err := odysseus.Refresh(ctx, s.odysseusOptions(), s.cfg.Odysseus.OutputFile, s.odysseusOtherSources())
	s.odysseus.lastRun = time.Now()
	if s.cfg.Odysseus.ChoicesPath != "" {
		if cs, cerr := odysseus.LoadChoices(s.cfg.Odysseus.ChoicesPath); cerr == nil {
			s.odysseus.choices = cs.Choices
		}
	}
	s.odysseus.lastModels = rr.Models
	s.odysseus.lastProfile = rr.Profiles
	s.odysseus.lastWrote = rr.Wrote
	if err != nil {
		s.odysseus.lastErr = err.Error()
		return rr, err
	}
	s.odysseus.lastErr = ""
	for _, w := range rr.Warnings {
		s.proxylog.Warnf("odysseus: %s", w)
	}
	return rr, nil
}

// startOdysseusRefresh runs one refresh at startup and then on a timer, tied to
// the server's shutdown context so a hot config reload (which builds a new
// Server) does not leave the old timer running.
//
// A write never reloads in place: it relies on the same -watch-config watcher
// modelscan does. Without -watch-config the file is still written, but nothing
// picks it up until a restart or another reload trigger fires.
func (s *Server) startOdysseusRefresh() {
	if !s.odysseusEnabled() {
		return
	}
	cfg := s.cfg.Odysseus

	go func() {
		ctx, cancel := context.WithTimeout(s.shutdownCtx, time.Duration(cfg.TimeoutSeconds)*time.Second)
		defer cancel()
		rr, err := s.runOdysseusRefresh(ctx)
		if err != nil {
			s.proxylog.Warnf("odysseus: startup refresh failed: %v", err)
			return
		}
		s.proxylog.Infof("odysseus: startup refresh found %d variant(s) in %d profile(s), config changed=%v",
			rr.Models, rr.Profiles, rr.Wrote)
	}()

	if cfg.RefreshSeconds <= 0 {
		s.proxylog.Infof("odysseus: refresh timer disabled (refreshSeconds: 0); " +
			"use POST /api/odysseus/refresh or GET /v1/models to refresh")
		return
	}

	go func() {
		ticker := time.NewTicker(time.Duration(cfg.RefreshSeconds) * time.Second)
		defer ticker.Stop()
		for {
			select {
			case <-s.shutdownCtx.Done():
				return
			case <-ticker.C:
				ctx, cancel := context.WithTimeout(s.shutdownCtx, time.Duration(cfg.TimeoutSeconds)*time.Second)
				rr, err := s.runOdysseusRefresh(ctx)
				cancel()
				if err != nil {
					s.proxylog.Warnf("odysseus: periodic refresh failed: %v", err)
					continue
				}
				if rr.Wrote {
					s.proxylog.Infof("odysseus: refresh found %d variant(s) in %d profile(s), config changed",
						rr.Models, rr.Profiles)
				}
			}
		}
	}()
}

// triggerBackgroundOdysseusRefresh refreshes out of band, coalescing so that a
// burst of /v1/models calls produces at most one refresh per
// odysseusMinTriggerInterval. Fire-and-forget: a model listing must not get
// slower or fail because Odysseus is unreachable.
func (s *Server) triggerBackgroundOdysseusRefresh() {
	if !s.odysseusEnabled() {
		return
	}
	s.odysseus.mu.Lock()
	tooSoon := time.Since(s.odysseus.lastRun) < odysseusMinTriggerInterval
	s.odysseus.mu.Unlock()
	if tooSoon {
		return
	}

	go func() {
		ctx, cancel := context.WithTimeout(s.shutdownCtx, time.Duration(s.cfg.Odysseus.TimeoutSeconds)*time.Second)
		defer cancel()
		rr, err := s.runOdysseusRefresh(ctx)
		if err != nil {
			s.proxylog.Warnf("odysseus: background refresh failed: %v", err)
			return
		}
		if rr.Wrote {
			s.proxylog.Infof("odysseus: background refresh found %d variant(s) in %d profile(s), config changed",
				rr.Models, rr.Profiles)
		}
	}()
}

// handleAPIOdysseusRefresh is the explicit, synchronous trigger:
// POST /api/odysseus/refresh. The UI's refresh control uses it so the caller
// gets a direct answer instead of waiting on the timer.
func (s *Server) handleAPIOdysseusRefresh(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")

	if !s.odysseusEnabled() {
		w.WriteHeader(http.StatusServiceUnavailable)
		json.NewEncoder(w).Encode(map[string]any{
			"ok":    false,
			"error": "odysseus is not enabled in config",
		})
		return
	}

	ctx, cancel := context.WithTimeout(r.Context(), time.Duration(s.cfg.Odysseus.TimeoutSeconds)*time.Second)
	defer cancel()

	rr, err := s.runOdysseusRefresh(ctx)
	if err != nil {
		s.proxylog.Warnf("odysseus: refresh failed: %v", err)
		w.WriteHeader(http.StatusBadGateway)
		json.NewEncoder(w).Encode(map[string]any{
			"ok":       false,
			"error":    err.Error(),
			"warnings": rr.Warnings,
		})
		return
	}

	s.proxylog.Infof("odysseus: refresh found %d variant(s) in %d profile(s), config changed=%v",
		rr.Models, rr.Profiles, rr.Wrote)
	json.NewEncoder(w).Encode(map[string]any{
		"ok":       true,
		"models":   rr.Models,
		"profiles": rr.Profiles,
		"changed":  rr.Wrote,
		"warnings": rr.Warnings,
	})
}

// handleAPIOdysseusStatus reports the integration's configuration and last run,
// so the UI can show whether it is healthy without triggering a refresh:
// GET /api/odysseus/status.
func (s *Server) handleAPIOdysseusStatus(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")

	if !s.odysseusEnabled() {
		json.NewEncoder(w).Encode(map[string]any{"enabled": false})
		return
	}
	cfg := s.cfg.Odysseus

	s.odysseus.mu.Lock()
	lastRun := s.odysseus.lastRun
	lastErr := s.odysseus.lastErr
	models := s.odysseus.lastModels
	profiles := s.odysseus.lastProfile
	wrote := s.odysseus.lastWrote
	s.odysseus.mu.Unlock()

	resp := map[string]any{
		"enabled":        true,
		"baseURL":        cfg.BaseURL,
		"outputFile":     cfg.OutputFile,
		"profilePrefix":  cfg.ProfilePrefix,
		"refreshSeconds": cfg.RefreshSeconds,
		"models":         models,
		"profiles":       profiles,
		"changed":        wrote,
	}
	if lastErr != "" {
		resp["error"] = lastErr
	}
	if !lastRun.IsZero() {
		resp["lastRun"] = lastRun.UTC().Format(time.RFC3339)
	}
	json.NewEncoder(w).Encode(resp)
}

// handleAPIOdysseusModelProfile sets or clears the Odysseus preset a single
// model launches with: PUT /api/odysseus/model/{model}/profile, body
// {"label":"<preset label>"} or {"label":null} to clear.
//
// The choice is persisted immediately and a refresh is triggered, which
// regenerates the union profile with a pin for exactly this model. Models
// without a choice keep llama-swap's own launch -- nothing carries over.
func (s *Server) handleAPIOdysseusModelProfile(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	o := s.cfg.Odysseus
	if o == nil || !o.Enabled {
		w.WriteHeader(http.StatusServiceUnavailable)
		json.NewEncoder(w).Encode(map[string]any{"ok": false, "error": "odysseus is not enabled in config"})
		return
	}
	modelID := r.PathValue("model")
	if strings.TrimSpace(modelID) == "" {
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(map[string]any{"ok": false, "error": "model path value is required"})
		return
	}

	var body struct {
		Label *string `json:"label"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(map[string]any{"ok": false, "error": "invalid request body"})
		return
	}

	label := ""
	if body.Label != nil {
		label = strings.TrimSpace(*body.Label)
	}

	if label != "" {
		// the label must map to a variant that actually exists for this model,
		// otherwise the pin would dangle and requests would 404 after the reload
		variant := modelID + "--" + odysseus.SanitizeProfileLabel(label)
		if _, ok := s.cfg.Models[variant]; !ok {
			w.WriteHeader(http.StatusNotFound)
			json.NewEncoder(w).Encode(map[string]any{
				"ok":    false,
				"error": fmt.Sprintf("no saved Odysseus config %q for model %q", label, modelID),
			})
			return
		}
	}

	if o.ChoicesPath == "" {
		w.WriteHeader(http.StatusServiceUnavailable)
		json.NewEncoder(w).Encode(map[string]any{"ok": false, "error": "odysseus.choicesPath is not configured"})
		return
	}

	cs, err := odysseus.LoadChoices(o.ChoicesPath)
	if err != nil {
		w.WriteHeader(http.StatusInternalServerError)
		json.NewEncoder(w).Encode(map[string]any{"ok": false, "error": err.Error()})
		return
	}
	if label == "" {
		delete(cs.Choices, modelID)
	} else {
		cs.Choices[modelID] = label
	}
	if err := odysseus.SaveChoices(o.ChoicesPath, cs); err != nil {
		w.WriteHeader(http.StatusInternalServerError)
		json.NewEncoder(w).Encode(map[string]any{"ok": false, "error": err.Error()})
		return
	}

	// regenerate now so the union profile reflects the new pin set; the write
	// trips -watch-config, whose reload is what makes the profile live.
	go func() {
		ctx, cancel := context.WithTimeout(s.shutdownCtx, time.Duration(o.TimeoutSeconds)*time.Second)
		defer cancel()
		if _, err := s.runOdysseusRefresh(ctx); err != nil {
			s.proxylog.Warnf("odysseus: refresh after choice change: %v", err)
		}
	}()

	json.NewEncoder(w).Encode(map[string]any{
		"ok":    true,
		"model": modelID,
		"label": label,
	})
}

// handleAPIOdysseusModelProfiles lists the saved-config labels available for a
// model and the model's current choice:
// GET /api/odysseus/model/{model}/profile
//
// Labels are derived from the config (variants are unlisted models named
// <base>--<label>), so the list is always in sync with the last refresh.
func (s *Server) handleAPIOdysseusModelProfiles(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	o := s.cfg.Odysseus
	if o == nil || !o.Enabled {
		json.NewEncoder(w).Encode(map[string]any{"enabled": false})
		return
	}
	modelID := r.PathValue("model")

	prefix := modelID + "--"
	labels := make([]string, 0, 8)
	for id := range s.cfg.Models {
		if strings.HasPrefix(id, prefix) {
			labels = append(labels, strings.TrimPrefix(id, prefix))
		}
	}
	sort.Strings(labels)

	chosen := ""
	if s.odysseus.choices != nil {
		chosen = s.odysseus.choices[modelID]
	}
	json.NewEncoder(w).Encode(map[string]any{
		"model":   modelID,
		"labels":  labels,
		"chosen":  chosen,
		"enabled": true,
	})
}
