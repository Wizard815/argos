package server

import (
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"strings"

	"github.com/mostlygeek/llama-swap/internal/event"
	"github.com/mostlygeek/llama-swap/internal/swaputil"
)

// A model's GPU toggle mode is per-model runtime state chosen in the model
// detail view:
//
//	standard     leave the effective launch exactly as written (the opt-out:
//	             this model is not meant to use either toggle)
//	single_gpu   bind to whichever card actually has enough free VRAM
//	multi_model  run several instances, one per GPU, ceil(chats/parallel)
//
// It is kept in cfg.ModelTogglesFile so it survives a reload or a container
// recreate. An empty ModelTogglesFile disables the control.

const (
	// ModeStandard is the default and the opt-out.
	ModeStandard = "standard"
	// ModeSingleGPU picks a card by free VRAM at launch.
	ModeSingleGPU = "single_gpu"
	// ModeMultiModel scales the model to several instances, one per GPU.
	ModeMultiModel = "multi_model"
)

// modelModes is the ordered list of valid modes, used for validation and to
// tell the UI what to offer.
var modelModes = []string{ModeStandard, ModeSingleGPU, ModeMultiModel}

func validModelMode(mode string) bool {
	switch mode {
	case ModeStandard, ModeSingleGPU, ModeMultiModel:
		return true
	}
	return false
}

// modelTogglesFile is the on-disk shape. Only a model whose mode is not
// standard needs an entry, so a cleared choice is stored by deleting the key.
type modelTogglesFile struct {
	Modes map[string]string `json:"modes"`
}

// loadModelToggles reads the persisted modes. A missing file is not an error
// (first run); an unreadable or malformed one is reported so a bad mount is
// visible rather than silently treated as "everything standard".
func loadModelToggles(path string) (map[string]string, error) {
	out := map[string]string{}
	if path == "" {
		return out, nil
	}
	data, err := os.ReadFile(path)
	if err != nil {
		if os.IsNotExist(err) {
			return out, nil
		}
		return out, err
	}
	var tf modelTogglesFile
	if err := json.Unmarshal(data, &tf); err != nil {
		return out, fmt.Errorf("parsing %s: %w", path, err)
	}
	for id, mode := range tf.Modes {
		if validModelMode(mode) {
			out[id] = mode
		}
	}
	return out, nil
}

// saveModelToggles writes the modes atomically, so a half-written file is never
// what the next start reads back and the write works through a bind mount.
func saveModelToggles(path string, modes map[string]string) error {
	if path == "" {
		return nil
	}
	if dir := filepath.Dir(path); dir != "" && dir != "." {
		if err := os.MkdirAll(dir, 0o755); err != nil {
			return fmt.Errorf("creating model toggles dir: %w", err)
		}
	}
	data, err := json.MarshalIndent(modelTogglesFile{Modes: modes}, "", "  ")
	if err != nil {
		return err
	}
	tmp := path + ".tmp"
	if err := os.WriteFile(tmp, append(data, '\n'), 0o644); err != nil {
		return fmt.Errorf("writing %s: %w", path, err)
	}
	return os.Rename(tmp, path)
}

// ModelMode returns a model's mode, defaulting to standard.
func (s *Server) ModelMode(modelID string) string {
	s.modelModesMu.RLock()
	defer s.modelModesMu.RUnlock()
	if m, ok := s.modelModes[modelID]; ok && validModelMode(m) {
		return m
	}
	return ModeStandard
}

// restoreModelToggles records the modes loaded during startup (and possibly
// extended by applyMultiModel), before the first modelStatus is served, so the
// detail view renders the right control on first paint.
func (s *Server) restoreModelToggles(modes map[string]string) {
	if modes == nil {
		modes = map[string]string{}
	}
	s.modelModesMu.Lock()
	s.modelModes = modes
	s.modelModesMu.Unlock()
	if len(modes) > 0 {
		s.proxylog.Infof("model toggles: %d mode(s) active from %s", len(modes), s.cfg.ModelTogglesFile)
	}
}

// handleAPIModelMode sets one model's GPU toggle mode:
//
//	PUT /api/models/{model}/mode   body {"mode":"standard"|"single_gpu"|"multi_model"}
func (s *Server) handleAPIModelMode(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	if s.cfg.ModelTogglesFile == "" {
		w.WriteHeader(http.StatusServiceUnavailable)
		json.NewEncoder(w).Encode(map[string]any{"ok": false,
			"error": "modelTogglesFile is not configured"})
		return
	}

	modelID := strings.TrimPrefix(r.PathValue("model"), "/")
	if strings.TrimSpace(modelID) == "" {
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(map[string]any{"ok": false, "error": "model is required"})
		return
	}
	// Management endpoints address the concrete model.
	real, found := s.cfg.RealModelName(modelID)
	if !found {
		w.WriteHeader(http.StatusNotFound)
		json.NewEncoder(w).Encode(map[string]any{"ok": false, "error": "model not found"})
		return
	}

	var body struct {
		Mode string `json:"mode"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(map[string]any{"ok": false, "error": "invalid request body"})
		return
	}
	mode := strings.TrimSpace(body.Mode)
	if !validModelMode(mode) {
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(map[string]any{"ok": false,
			"error": fmt.Sprintf("mode must be one of %s", strings.Join(modelModes, ", "))})
		return
	}

	s.modelModesMu.Lock()
	if s.modelModes == nil {
		s.modelModes = map[string]string{}
	}
	if mode == ModeStandard {
		delete(s.modelModes, real) // standard is the default; a key would only add noise
	} else {
		s.modelModes[real] = mode
	}
	snapshot := make(map[string]string, len(s.modelModes))
	for k, v := range s.modelModes {
		snapshot[k] = v
	}
	s.modelModesMu.Unlock()

	if err := saveModelToggles(s.cfg.ModelTogglesFile, snapshot); err != nil {
		s.proxylog.Warnf("model toggles: %v", err)
		w.WriteHeader(http.StatusInternalServerError)
		json.NewEncoder(w).Encode(map[string]any{"ok": false, "error": err.Error()})
		return
	}

	// The model list carries each model's mode, so tell the SSE listeners to
	// re-send it. Without this the detail view keeps the previous highlight
	// until some unrelated event happens to refresh the list.
	event.Emit(swaputil.ConfigFileChangedEvent{State: swaputil.ReloadingStateEnd})

	json.NewEncoder(w).Encode(map[string]any{"ok": true, "model": real, "mode": mode})
}

// handleAPIModelModes lists the modes in use plus the valid set, so a client
// can render the control without hard-coding the list:
//
//	GET /api/models/modes
func (s *Server) handleAPIModelModes(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	s.modelModesMu.RLock()
	out := make(map[string]string, len(s.modelModes))
	for k, v := range s.modelModes {
		out[k] = v
	}
	s.modelModesMu.RUnlock()
	json.NewEncoder(w).Encode(map[string]any{
		"ok":      true,
		"modes":   out,
		"valid":   modelModes,
		"enabled": s.cfg.ModelTogglesFile != "",
	})
}
