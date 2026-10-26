package server

import (
	"net/http"
	"strings"

	"github.com/Wizard815/Argos/internal/chain"
	"github.com/Wizard815/Argos/internal/swaputil"
)

// CreateProfileMiddleware applies the request's active profile snapshot before
// the normal request pipeline resolves the rewritten model.
func CreateProfileMiddleware(s *Server) chain.Middleware {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			if pins := s.requestPins(); len(pins) > 0 {
				var model, replacement string
				var pinned bool
				if strings.HasPrefix(r.URL.Path, "/upstream/") {
					model, replacement, pinned = upstreamProfilePin(r.PathValue("upstreamPath"), pins)
					if pinned && s.ModelMode(model) == ModeMultiModel {
						replacement, pinned = "", false
					}
				} else {
					model, _ = swaputil.ExtractModel(r)
					replacement, pinned = s.requestPin(model)
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
	target, pinned := s.requestPin(id)
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
	if target, pinned := s.requestPin(id); pinned && target != "" {
		return target
	}
	return id
}

// requestPin is where a request for a model goes: the active profile's pin,
// else the profile picked for that model (its Odysseus choice). Before, a
// choice only changed what the UI reported; requests kept running the base
// model's own command unless the generated union profile was also activated,
// and that activation does not survive a restart.
func (s *Server) requestPin(model string) (string, bool) {
	if target, pinned := s.profilePin(model); pinned {
		return target, true
	}
	if model == "" || s.ModelMode(model) == ModeMultiModel {
		return "", false
	}
	if label := s.chosenLabel(model); label != "" {
		if _, ok := s.cfg.Models[model+"--"+label]; ok {
			return model + "--" + label, true
		}
	}
	return "", false
}

// requestPins is every rewrite requestPin would make, for the /upstream path
// match: the active profile's pins, plus each model's chosen profile.
func (s *Server) requestPins() map[string]string {
	pins := map[string]string{}
	if profile, ok := s.cfg.Profiles[s.ActiveProfile()]; ok {
		for k, v := range profile.Pins {
			pins[k] = v
		}
	}
	for model, label := range s.allChoices() {
		if _, taken := pins[model]; taken || label == "" || s.ModelMode(model) == ModeMultiModel {
			continue
		}
		if _, ok := s.cfg.Models[model+"--"+label]; ok {
			pins[model] = model + "--" + label
		}
	}
	return pins
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
