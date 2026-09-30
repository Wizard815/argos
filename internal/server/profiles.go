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
				} else {
					model, _ = swaputil.ExtractModel(r)
					if model != "" {
						replacement, pinned = profile.Pins[model]
					}
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
	profile, ok := s.cfg.Profiles[s.ActiveProfile()]
	if !ok {
		return cmd
	}
	target, pinned := profile.Pins[id]
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
	if profile, ok := s.cfg.Profiles[s.ActiveProfile()]; ok {
		if target, pinned := profile.Pins[id]; pinned && target != "" {
			return target
		}
	}
	if chosen := s.odysseus.choices[id]; chosen != "" {
		if _, ok := s.cfg.Models[id+"--"+chosen]; ok {
			return id + "--" + chosen
		}
	}
	return id
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
