package config

import (
	"fmt"

	"github.com/Wizard815/Argos/internal/tailcat"
)

const TailcatEphemeralKey = "ephemeral"

// TailcatConfig configures the private Tailcat listener and its HTTP policy.
// The listener identity comes from the startup-only -listen-tailcat flag.
type TailcatConfig struct {
	Allow  []string `yaml:"allow"`
	Models []string `yaml:"models"`
	Admin  bool     `yaml:"admin"`
	Debug  bool     `yaml:"debug"`

	AllowedClients []string `yaml:"-"`
}

func validateTailcatConfig(cfg *Config) error {
	if tc := cfg.Tailcat; tc != nil {
		seenAllow := make(map[string]struct{}, len(tc.Allow))
		allowedClients := make([]string, 0, len(tc.Allow))
		for i, raw := range tc.Allow {
			canonical, err := tailcat.ValidateNodePublic(raw)
			if err != nil {
				return fmt.Errorf("tailcat.allow[%d]: invalid node public key: %w", i, err)
			}
			if canonical != raw {
				return fmt.Errorf("tailcat.allow[%d]: must be a full canonical nodekey: public key", i)
			}
			if _, exists := seenAllow[canonical]; exists {
				continue
			}
			seenAllow[canonical] = struct{}{}
			allowedClients = append(allowedClients, canonical)
		}
		tc.Allow = allowedClients
		tc.AllowedClients = allowedClients

		validModels := publicTailcatModelIDs(*cfg)
		seenModels := make(map[string]struct{}, len(tc.Models))
		models := make([]string, 0, len(tc.Models))
		for i, model := range tc.Models {
			if model == "" {
				return fmt.Errorf("tailcat.models[%d]: model ID cannot be empty", i)
			}
			if model != "*" {
				if _, ok := validModels[model]; !ok {
					return fmt.Errorf("tailcat.models[%d]: unknown public model ID %q", i, model)
				}
			}
			if _, exists := seenModels[model]; exists {
				continue
			}
			seenModels[model] = struct{}{}
			models = append(models, model)
		}
		tc.Models = models
	}
	return nil
}

func publicTailcatModelIDs(cfg Config) map[string]struct{} {
	ids := make(map[string]struct{})
	for id, model := range cfg.Models {
		ids[id] = struct{}{}
		for _, alias := range model.Aliases {
			ids[alias] = struct{}{}
		}
	}
	for id := range cfg.Selectors {
		ids[id] = struct{}{}
	}
	for _, profile := range cfg.Profiles {
		for pin := range profile.Pins {
			ids[pin] = struct{}{}
		}
	}
	return ids
}
