package server

import (
	"net/http"
	"strings"

	"github.com/mostlygeek/llama-swap/internal/chain"
	"github.com/mostlygeek/llama-swap/internal/swaputil"
)

// CreateProfileMiddleware applies the request's active profile snapshot before
// the normal request pipeline resolves the rewritten model.
func CreateProfileMiddleware(s *Server) chain.Middleware {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			profile, ok := s.cfg.Profiles[s.ActiveProfile()]
			if ok {
				var model, replacement string
				var pinned bool
				if strings.HasPrefix(r.URL.Path, "/upstream/") {
					model, replacement, pinned = upstreamProfilePin(r.PathValue("upstreamPath"), profile.Pins)
					if pinned && s.ModelMode(model) == ModeMultiModel {
						replacement, pinned = "", false
					}
				} else {
					model, _ = swaputil.ExtractModel(r)
					replacement, pinned = s.profilePin(model)
				}
				if pinned {
					updated, err := swaputil.ReplaceRequestModel(r, model, replacement)
					if err != nil {
						swaputil.SendResponse(w, r, http.StatusBadRequest, err.Error())
						return
					}
					r = updated
				}
			}

			next.ServeHTTP(w, r)
		})
	}
}

// effectiveCommand returns the command that will actually run for a model id.
// An active profile's pin rewrites the request to a variant id before the
// pipeline resolves the model, so the pinned variant's command — not the base
// model's — decides the context window the client will be given.
func (s *Server) effectiveCommand(id, cmd string) string {
	target, pinned := s.profilePin(id)
	if !pinned {
		return cmd
	}
	variant, ok := s.cfg.Models[target]
	if !ok || strings.TrimSpace(variant.Cmd) == "" {
		return cmd
	}
	return variant.Cmd
}

// effectiveModelID returns the id a request for this model would actually
// resolve to: the active profile's pin when one applies, otherwise the Odysseus
// per-model choice. The variant - not the base model - is the process that
// serves such a request, so everything the UI reports about that process, the
// running state above all, has to follow the same resolution as the effective
// command shown on the same card.
func (s *Server) effectiveModelID(id string) string {
	if target, pinned := s.profilePin(id); pinned && target != "" {
		return target
	}
	if chosen := s.odysseus.choices[id]; chosen != "" {
		if _, ok := s.cfg.Models[id+"--"+chosen]; ok {
			return id + "--" + chosen
		}
	}
	return id
}

// profilePin returns the active profile's pin for a model. A multi_model model
// is served by its spillover selector, and a pin rewrites the id before the
// selector resolves it — which would bypass the selector entirely — so the pin
// is declined for those models. Turning the mode off restores the pin.
func (s *Server) profilePin(model string) (string, bool) {
	if model == "" || s.ModelMode(model) == ModeMultiModel {
		return "", false
	}
	profile, ok := s.cfg.Profiles[s.ActiveProfile()]
	if !ok {
		return "", false
	}
	target, pinned := profile.Pins[model]
	return target, pinned
}

// baseModelID returns the model a variant belongs to, or "" when the id is not a
// variant of a configured model. The odysseus integration names variants
// <base>--<label>, and the UI links a variant back to its base model so a
// profile swap reads as one model instead of a second one.
func (s *Server) baseModelID(id string) string {
	if isGeneratedCopy(id) {
		return "" // a multi_model copy is generated state, not a variant of anything
	}
	base, _, ok := strings.Cut(id, "--")
	if !ok || base == "" {
		return ""
	}
	if _, exists := s.cfg.Models[base]; !exists {
		return ""
	}
	return base
}

func upstreamProfilePin(upstreamPath string, pins map[string]string) (model, replacement string, found bool) {
	upstreamPath = strings.TrimPrefix(upstreamPath, "/")
	matchedPin := ""
	for pin, candidate := range pins {
		switch {
		case upstreamPath == pin:
			if len(pin) > len(matchedPin) {
				matchedPin = pin
				replacement = candidate
			}
		case strings.HasPrefix(upstreamPath, pin+"/"):
			if len(pin) > len(matchedPin) {
				matchedPin = pin
				replacement = candidate
			}
		}
	}
	return matchedPin, replacement, matchedPin != ""
}
