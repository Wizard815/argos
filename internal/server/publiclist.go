package server

import (
	"encoding/json"
	"net/http"
	"sort"
	"strings"

	"github.com/Wizard815/Argos/internal/chain"
	"github.com/Wizard815/Argos/internal/config"
	"github.com/Wizard815/Argos/internal/event"
	"github.com/Wizard815/Argos/internal/modelscan"
	"github.com/Wizard815/Argos/internal/swaputil"
)

// The model list apps see. Argos hosts every GGUF in the models folder, but an
// app should only see and call the models someone made: the profiles. So, for
// a request from outside (one the builder forwarded, with X-Forwarded-For):
//
//   - a base model (one the scan found on disk) is not listed, and a call for
//     it is refused, unless it has a default profile (the call runs that) or it
//     was listed by hand (PUT /api/models/{model}/listed);
//   - a profile <base>--<label> is listed by its label alone, and a call for
//     the label runs it. A label two bases share keeps its full id.
//
// Requests from inside (the builder, the engine's own pages) see everything.
//
// Two settings change this (PUT /api/models/public-list, saved in
// modelTogglesFile):
//
//   - names "full" lists a profile as <base>--<label> instead of its label;
//   - bases "unprofiled" also lists each base model that has no profile, under
//     its own name, and "all" lists every base model.

// publicListSettings is how outside clients see the model list.
type publicListSettings struct {
	Names string `json:"names"` // "label" (default) or "full"
	Bases string `json:"bases"` // "profiles" (default), "unprofiled" or "all"
}

func (p publicListSettings) normal() publicListSettings {
	if p.Names != "full" {
		p.Names = "label"
	}
	if p.Bases != "unprofiled" && p.Bases != "all" {
		p.Bases = "profiles"
	}
	return p
}

func (s *Server) publicSettings() publicListSettings {
	s.listedMu.RLock()
	defer s.listedMu.RUnlock()
	return s.publicList.normal()
}

// hasProfile reports whether a base model has a profile <base>--<label>.
func (s *Server) hasProfile(base string) bool {
	for id := range s.cfg.Models {
		if strings.HasPrefix(id, base+"--") && !isGeneratedCopy(id) {
			return true
		}
	}
	return false
}

// isBaseModel reports whether the model scan generated this model.
func isBaseModel(mc config.ModelConfig) bool {
	v, _ := mc.Metadata[modelscan.BaseModelKey].(bool)
	return v
}

// fromOutside reports whether the builder forwarded this request from a
// client; its own calls, and the engine's, come straight to 127.0.0.1.
func fromOutside(r *http.Request) bool {
	return r.Header.Get("X-Forwarded-For") != "" || r.Header.Get("Forwarded") != ""
}

// listedBase reports whether a base model was listed by hand.
func (s *Server) listedBase(id string) bool {
	s.listedMu.RLock()
	defer s.listedMu.RUnlock()
	return s.listed[id]
}

// shortNames maps each profile's label to its full id <base>--<label>, for the
// labels only one profile has and no model or alias already uses.
func (s *Server) shortNames() map[string]string {
	seen := map[string][]string{}
	for id := range s.cfg.Models {
		base, label, ok := strings.Cut(id, "--")
		if !ok || label == "" || isGeneratedCopy(id) {
			continue
		}
		if _, known := s.cfg.Models[base]; !known {
			continue
		}
		seen[label] = append(seen[label], id)
	}
	out := map[string]string{}
	for label, ids := range seen {
		if len(ids) != 1 {
			continue
		}
		if _, taken := s.cfg.RealModelName(label); taken {
			continue
		}
		if _, taken := s.cfg.Selectors[label]; taken {
			continue
		}
		out[label] = ids[0]
	}
	return out
}

// publicName is the id a profile is listed under for an outside client.
func publicName(id string, short map[string]string, names string) string {
	if names == "full" {
		return id
	}
	if _, label, ok := strings.Cut(id, "--"); ok && short[label] == id {
		return label
	}
	return id
}

// unlistedForOutside reports whether an outside client does not see this
// model in the list: a base model not listed by hand. Its profiles are listed.
func (s *Server) unlistedForOutside(id string, mc config.ModelConfig) bool {
	if !isBaseModel(mc) || s.listedBase(id) {
		return false
	}
	switch s.publicSettings().Bases {
	case "all":
		return false
	case "unprofiled":
		return s.hasProfile(id)
	}
	return true
}

// hiddenFromOutside reports whether an outside client may not call this model:
// unlisted, with no default profile for the call to run.
func (s *Server) hiddenFromOutside(id string, mc config.ModelConfig) bool {
	if !s.unlistedForOutside(id, mc) {
		return false
	}
	_, pinned := s.requestPin(id)
	return !pinned
}

// CreateShortNameMiddleware runs a profile asked for by its label on
// /upstream/<label>/...: the same names apps see in the list work there too.
func CreateShortNameMiddleware(s *Server) chain.Middleware {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			model, _, _ := strings.Cut(strings.TrimPrefix(r.URL.Path, "/upstream/"), "/")
			if model != "" {
				if _, known := s.cfg.RealModelName(model); !known {
					if full, ok := s.shortNames()[model]; ok {
						if updated, err := swaputil.ReplaceRequestModel(r, model, full); err == nil {
							r = updated
						}
					}
				}
			}
			next.ServeHTTP(w, r)
		})
	}
}

// CreatePublicModelMiddleware runs a profile asked for by its label, and turns
// away an outside call for a base model that has no profile.
func CreatePublicModelMiddleware(s *Server) chain.Middleware {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			model, _ := swaputil.ExtractModel(r)
			if model != "" {
				if _, known := s.cfg.RealModelName(model); !known {
					if full, ok := s.shortNames()[model]; ok {
						updated, err := swaputil.ReplaceRequestModel(r, model, full)
						if err != nil {
							swaputil.SendResponse(w, r, http.StatusBadRequest, err.Error())
							return
						}
						r, model = updated, full
					}
				}
				if fromOutside(r) {
					if id, ok := s.cfg.RealModelName(model); ok && s.hiddenFromOutside(id, s.cfg.Models[id]) {
						swaputil.SendResponse(w, r, http.StatusNotFound, "model "+model+
							" is a base model: make a profile for it in Argos (Models -> Prefab), "+
							"or turn on \"Show the base model\" for it")
						return
					}
				}
			}
			next.ServeHTTP(w, r)
		})
	}
}

// handleAPIModelListed lists a base model by hand, or hides it again:
//
//	PUT /api/models/{model}/listed   body {"listed": true|false}
//	GET /api/models/listed           the base models listed by hand
func (s *Server) handleAPIModelListed(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	fail := func(code int, msg string) {
		w.WriteHeader(code)
		json.NewEncoder(w).Encode(map[string]any{"ok": false, "error": msg})
	}
	if r.Method == http.MethodGet {
		json.NewEncoder(w).Encode(map[string]any{"ok": true, "listed": s.listedIDs(), "enabled": s.cfg.ModelTogglesFile != ""})
		return
	}
	if s.cfg.ModelTogglesFile == "" {
		fail(http.StatusServiceUnavailable, "modelTogglesFile is not configured")
		return
	}
	id, found := s.cfg.RealModelName(strings.TrimPrefix(r.PathValue("model"), "/"))
	if !found {
		fail(http.StatusNotFound, "model not found")
		return
	}
	var body struct {
		Listed bool `json:"listed"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		fail(http.StatusBadRequest, "invalid request body")
		return
	}
	s.listedMu.Lock()
	if s.listed == nil {
		s.listed = map[string]bool{}
	}
	if body.Listed {
		s.listed[id] = true
	} else {
		delete(s.listed, id)
	}
	s.listedMu.Unlock()
	togglesFileMu.Lock()
	tf := readTogglesFile(s.cfg.ModelTogglesFile)
	tf.Listed = s.listedIDs()
	err := writeTogglesFile(s.cfg.ModelTogglesFile, tf)
	togglesFileMu.Unlock()
	if err != nil {
		fail(http.StatusInternalServerError, err.Error())
		return
	}
	event.Emit(swaputil.ConfigFileChangedEvent{State: swaputil.ReloadingStateEnd})
	json.NewEncoder(w).Encode(map[string]any{"ok": true, "model": id, "listed": body.Listed})
}

// handleAPIPublicList reads or changes how outside clients see the model list:
//
//	GET /api/models/public-list
//	PUT /api/models/public-list   body {"names": "label"|"full", "bases": "profiles"|"unprofiled"|"all"}
func (s *Server) handleAPIPublicList(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	if r.Method == http.MethodPut {
		cur := s.publicSettings()
		var body struct {
			Names *string `json:"names"`
			Bases *string `json:"bases"`
		}
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
			w.WriteHeader(http.StatusBadRequest)
			json.NewEncoder(w).Encode(map[string]any{"ok": false, "error": "invalid request body"})
			return
		}
		if body.Names != nil {
			cur.Names = *body.Names
		}
		if body.Bases != nil {
			cur.Bases = *body.Bases
		}
		cur = cur.normal()
		s.listedMu.Lock()
		s.publicList = cur
		s.listedMu.Unlock()
		if s.cfg.ModelTogglesFile != "" {
			togglesFileMu.Lock()
			tf := readTogglesFile(s.cfg.ModelTogglesFile)
			tf.PublicList = &cur
			err := writeTogglesFile(s.cfg.ModelTogglesFile, tf)
			togglesFileMu.Unlock()
			if err != nil {
				w.WriteHeader(http.StatusInternalServerError)
				json.NewEncoder(w).Encode(map[string]any{"ok": false, "error": err.Error()})
				return
			}
		}
		event.Emit(swaputil.ConfigFileChangedEvent{State: swaputil.ReloadingStateEnd})
	}
	p := s.publicSettings()
	json.NewEncoder(w).Encode(map[string]any{"ok": true, "names": p.Names, "bases": p.Bases, "saved": s.cfg.ModelTogglesFile != ""})
}

func (s *Server) listedIDs() []string {
	s.listedMu.RLock()
	defer s.listedMu.RUnlock()
	out := make([]string, 0, len(s.listed))
	for id := range s.listed {
		out = append(out, id)
	}
	sort.Strings(out)
	return out
}

// restoreListed loads the base models listed by hand.
func (s *Server) restoreListed() {
	tf := readTogglesFile(s.cfg.ModelTogglesFile)
	s.listedMu.Lock()
	s.listed = map[string]bool{}
	for _, id := range tf.Listed {
		s.listed[id] = true
	}
	if tf.PublicList != nil {
		s.publicList = tf.PublicList.normal()
	}
	s.listedMu.Unlock()
}
