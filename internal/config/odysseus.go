package config

import (
	"fmt"
	"net/url"
	"strings"
)

// OdysseusConfig wires llama-swap to an Odysseus instance so Odysseus can stay
// the author of a model's launch command while llama-swap remains the process
// supervisor.
//
// Odysseus records the command it launched for each model in
// cookbook_state.json (task.payload._cmd) and serves it from
// GET /api/cookbook/state. When this section is enabled, llama-swap polls that
// endpoint and renders a generated config fragment containing one variant model
// per (model, command) plus a llama-swap profile per command, whose `pins`
// rewrite the base model ID to that variant.
//
// The base models -- and therefore llama-swap's own default launch -- are never
// touched, so with no profile active (the state at startup and after every
// reload) nothing changes and the Odysseus commands are opt-in.
//
// This is a local fork addition (see internal/odysseus), not part of upstream
// llama-swap.
type OdysseusConfig struct {
	// Enabled turns on the integration: the refresh timer, the
	// GET /api/odysseus/status and POST /api/odysseus/refresh endpoints, and the
	// background refresh triggered by GET /v1/models. Defaults to false so
	// existing configs are unaffected.
	Enabled bool `yaml:"enabled"`

	// BaseURL is Odysseus' root, e.g. http://192.168.20.5:7005 from the host, or
	// http://odysseus:7000 when both containers share a Docker network.
	BaseURL string `yaml:"baseURL"`

	// Token is the value of ODYSSEUS_INTERNAL_TOKEN in the Odysseus container,
	// sent as the X-Odysseus-Internal-Token header (Odysseus' own tool layer
	// uses that header, and require_admin() accepts it).
	//
	// Prefer writing it as an env macro -- token: "${env.ODYSSEUS_INTERNAL_TOKEN}"
	// -- so the value lives in deploy/.env next to the rest of the deployment's
	// secrets instead of in config.yaml. TokenEnv reads it from the environment
	// at refresh time instead, and TokenFile from a mounted file.
	Token     string `yaml:"token"`
	TokenEnv  string `yaml:"tokenEnv"`
	TokenFile string `yaml:"tokenFile"`

	// RefreshSeconds is the polling interval. 0 disables the timer; the
	// integration then only refreshes on startup, on GET /v1/models and on
	// POST /api/odysseus/refresh. Defaults to 300.
	RefreshSeconds int `yaml:"refreshSeconds"`

	// ProfilePrefix names the generated profiles, so they are visibly distinct
	// from hand-written ones. Defaults to "ody".
	ProfilePrefix string `yaml:"profilePrefix"`

	// Statuses filters which Odysseus tasks are considered. Empty means both
	// "running" and "stopped", i.e. every command Odysseus has on record.
	Statuses []string `yaml:"statuses"`

	// OutputFile is the generated fragment. It must live in the directory passed
	// as -config-dir so llama-swap's merge (and the -watch-config watcher) picks
	// it up. Defaults to <profilePrefix>.generated.yaml beside modelscan's
	// output only when an absolute path is given; otherwise it must be set.
	OutputFile string `yaml:"outputFile"`

	// EnsureDio injects "-lm dio" into any command that has no load mode.
	// FEATURES.md: "Always pass -lm dio (--load-mode dio). mmap on the model file
	// hangs on this stack." Defaults to true; use a pointer so "unset" and
	// "explicitly false" are distinguishable.
	EnsureDio *bool `yaml:"ensureDio"`

	// TimeoutSeconds bounds a single HTTP call to Odysseus. Defaults to 20.
	TimeoutSeconds int `yaml:"timeoutSeconds"`
}

// DefaultOdysseusRefreshSeconds and friends keep the defaults in one place.
const (
	DefaultOdysseusRefreshSeconds = 300
	DefaultOdysseusProfilePrefix  = "ody"
	DefaultOdysseusTimeoutSeconds = 20
)

// SetDefaults fills in unset values. Called during config load.
func (o *OdysseusConfig) SetDefaults() {
	if o == nil {
		return
	}
	if o.RefreshSeconds == 0 {
		o.RefreshSeconds = DefaultOdysseusRefreshSeconds
	}
	if strings.TrimSpace(o.ProfilePrefix) == "" {
		o.ProfilePrefix = DefaultOdysseusProfilePrefix
	}
	if o.TimeoutSeconds == 0 {
		o.TimeoutSeconds = DefaultOdysseusTimeoutSeconds
	}
	if o.EnsureDio == nil {
		v := true
		o.EnsureDio = &v
	}
	if len(o.Statuses) == 0 {
		o.Statuses = []string{"running", "stopped"}
	}
}

// DioEnabled reports the effective EnsureDio value.
func (o *OdysseusConfig) DioEnabled() bool {
	if o == nil || o.EnsureDio == nil {
		return true
	}
	return *o.EnsureDio
}

// Validate rejects a config that could not work, at load time rather than at the
// first refresh.
func (o *OdysseusConfig) Validate() error {
	if o == nil || !o.Enabled {
		return nil
	}
	if strings.TrimSpace(o.BaseURL) == "" {
		return fmt.Errorf("odysseus.baseURL is required when odysseus.enabled is true")
	}
	u, err := url.Parse(o.BaseURL)
	if err != nil || u.Scheme == "" || u.Host == "" {
		return fmt.Errorf("odysseus.baseURL must be an absolute URL like http://odysseus:7000 (got %q)", o.BaseURL)
	}
	if u.Scheme != "http" && u.Scheme != "https" {
		return fmt.Errorf("odysseus.baseURL scheme must be http or https (got %q)", u.Scheme)
	}
	if strings.TrimSpace(o.OutputFile) == "" {
		return fmt.Errorf("odysseus.outputFile is required when odysseus.enabled is true, " +
			"and must be inside the -config-dir so the generated fragment is merged")
	}
	creds := 0
	for _, s := range []string{o.Token, o.TokenEnv, o.TokenFile} {
		if strings.TrimSpace(s) != "" {
			creds++
		}
	}
	if creds == 0 {
		return fmt.Errorf("odysseus needs a credential: set odysseus.token, odysseus.tokenEnv or odysseus.tokenFile. " +
			"Note Odysseus only exposes a stable token if ODYSSEUS_INTERNAL_TOKEN is set in its container -- " +
			"otherwise its token is random per process and cannot be read from outside")
	}
	if creds > 1 {
		return fmt.Errorf("odysseus: set only one of token, tokenEnv, tokenFile")
	}
	if o.RefreshSeconds < 0 {
		return fmt.Errorf("odysseus.refreshSeconds must be >= 0")
	}
	if o.TimeoutSeconds < 1 {
		return fmt.Errorf("odysseus.timeoutSeconds must be >= 1")
	}
	if strings.ContainsAny(o.ProfilePrefix, " \t") {
		return fmt.Errorf("odysseus.profilePrefix cannot contain whitespace")
	}
	for _, s := range o.Statuses {
		if strings.TrimSpace(s) == "" {
			return fmt.Errorf("odysseus.statuses cannot contain an empty value")
		}
	}
	return nil
}
