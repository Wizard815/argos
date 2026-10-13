// Package modelctl serves Argos's live model and profile state over MCP.
//
// It is the provider that makes Argos answerable about itself: which profile
// is active, what activating one would change, which models exist, and which
// of them are loaded right now. The state is read from the running Server
// through the State interface, so a hot reload builds a fresh provider over
// the state that is actually live -- the same property the config provider
// gets from its snapshot.
//
// Reading is free and safe. Changing the active profile and unloading models
// both mutate the running server, so those two tools are the ones a client
// should gate behind its own approval, and they are annotated that way.
package modelctl

import (
	"context"
	"encoding/json"
	"fmt"
	"sort"
	"strings"
	"time"

	"github.com/Wizard815/Argos/internal/mcptools"
)

// ProfileInfo is one configured profile as the provider reports it. Active is
// deliberately absent: which profile is active is a single server-wide fact
// and is read from ActiveProfile, so a list entry cannot disagree with it.
type ProfileInfo struct {
	Name        string
	Description string
}

// ModelInfo is one configured model. Running reflects whether a process for it
// is loaded right now; Proxy is the address it is reachable at once loaded,
// which is what a client needs to send a request to it.
type ModelInfo struct {
	ID          string
	Name        string
	Description string
	Proxy       string
	TTL         int
	Running     bool
}

// State is the slice of the running server this provider needs.
//
// It is an interface rather than a *server.Server so the provider can be
// tested without an HTTP server or a process supervisor, and so the server
// package does not have to be imported by the package that describes it.
type State interface {
	// Profiles lists the configured profiles in a stable order.
	Profiles() []ProfileInfo
	// ActiveProfile is the active profile name, or "" when none is active.
	ActiveProfile() string
	// SetActiveProfile selects a profile; an empty name clears the selection.
	// It returns an error for a name that is not configured.
	SetActiveProfile(name string) error
	// Models lists the configured models in a stable order.
	Models() []ModelInfo
	// UnloadAll stops every loaded model immediately.
	UnloadAll()
}

// Provider serves the models namespace.
type Provider struct {
	state State
}

var _ mcptools.Provider = (*Provider)(nil)

// NewProvider builds the provider over live server state.
func NewProvider(state State) *Provider {
	return &Provider{state: state}
}

func (p *Provider) ID() string { return "models" }

func (p *Provider) Shutdown(time.Duration) error { return nil }

// Reading the model list is read-only, but not idempotent: the answer changes
// as models load and unload, which is exactly the question being asked.
func readOnlyLiveAnnotations() *mcptools.Annotations {
	return &mcptools.Annotations{ReadOnlyHint: true, IdempotentHint: false}
}

// Reading the profile list is read-only and idempotent: a profile's
// description changes only when the configuration is reloaded.
func readOnlyStableAnnotations() *mcptools.Annotations {
	return &mcptools.Annotations{ReadOnlyHint: true, IdempotentHint: true}
}

// Selecting a profile and unloading models both change the running server, so
// neither is read-only. Both are idempotent: repeating the call reaches the
// same state.
func writeAnnotations() *mcptools.Annotations {
	return &mcptools.Annotations{ReadOnlyHint: false, IdempotentHint: true}
}

func (p *Provider) Tools(context.Context) ([]mcptools.Tool, error) {
	return []mcptools.Tool{
		{
			Name:  "list_profiles",
			Title: "List the configured Argos profiles",
			Description: "List every profile configured in Argos, with its description, and say which one is " +
				"active. A profile rewrites model ids to preconfigured variants, so activating one changes " +
				"which launch command serves each model without editing any configuration file. Use this " +
				"before set_active_profile to see the exact names that exist.",
			Annotations: readOnlyStableAnnotations(),
			Tags:        []string{"profiles", "routing", "models"},
			InputSchema: map[string]any{
				"type":                 "object",
				"properties":           map[string]any{},
				"additionalProperties": false,
			},
		},
		{
			Name:  "set_active_profile",
			Title: "Activate an Argos profile",
			Description: "Activate a profile by name, which is the same switch the web UI's profile selector " +
				"drives. The change applies to model launches that start after it, so a model that is already " +
				"loaded keeps the command it was started with until it is unloaded or its TTL expires. Pass an " +
				"empty name to clear the selection and fall back to no profile. An unknown name is reported as " +
				"an error and nothing changes.",
			Annotations: writeAnnotations(),
			Tags:        []string{"profiles", "routing", "models"},
			InputSchema: map[string]any{
				"type": "object",
				"properties": map[string]any{
					"name": map[string]any{
						"type": "string",
						"description": "Profile name to activate, exactly as list_profiles reports it. " +
							"Empty to clear the selection.",
					},
				},
				"required":             []string{"name"},
				"additionalProperties": false,
			},
		},
		{
			Name:  "list_models",
			Title: "List the configured Argos models",
			Description: "List every model configured in Argos with its id, display name, description, TTL and " +
				"whether it is loaded right now. This is the full catalog; use running_models when you only " +
				"need what is currently in memory.",
			Annotations: readOnlyLiveAnnotations(),
			Tags:        []string{"models", "inventory", "ttl"},
			InputSchema: map[string]any{
				"type":                 "object",
				"properties":           map[string]any{},
				"additionalProperties": false,
			},
		},
		{
			Name:  "running_models",
			Title: "List the models loaded right now",
			Description: "List the models that are loaded right now, each with the address it is reachable at " +
				"and its TTL. A model that is not loaded starts on its first request instead, so an empty " +
				"answer here is normal rather than a problem.",
			Annotations: readOnlyLiveAnnotations(),
			Tags:        []string{"models", "running", "memory"},
			InputSchema: map[string]any{
				"type":                 "object",
				"properties":           map[string]any{},
				"additionalProperties": false,
			},
		},
		{
			Name:  "unload_models",
			Title: "Unload every loaded model",
			Description: "Unload every loaded model immediately, freeing the GPU memory they hold. Argos also " +
				"unloads on each model's TTL; use this to free memory now, for example before starting a " +
				"larger model by hand. Models reload on their next request.",
			Annotations: writeAnnotations(),
			Tags:        []string{"models", "unload", "memory"},
			InputSchema: map[string]any{
				"type":                 "object",
				"properties":           map[string]any{},
				"additionalProperties": false,
			},
		},
	}, nil
}

func (p *Provider) Call(_ context.Context, name string, args map[string]json.RawMessage) (mcptools.Result, error) {
	switch name {
	case "list_profiles":
		return p.listProfiles(), nil
	case "set_active_profile":
		return p.setActiveProfile(args), nil
	case "list_models":
		return p.listModels(), nil
	case "running_models":
		return p.runningModels(), nil
	case "unload_models":
		return p.unloadModels(), nil
	default:
		return mcptools.Result{}, fmt.Errorf("unknown tool %q", name)
	}
}

func (p *Provider) listProfiles() mcptools.Result {
	profiles := p.state.Profiles()
	active := p.state.ActiveProfile()

	if len(profiles) == 0 {
		return mcptools.Result{Content: "No profiles are configured. Every model serves its own default " +
			"launch command. Add a profile to the \"profiles\" section of the Argos configuration to pin " +
			"models to variants."}
	}

	var b strings.Builder
	if active == "" {
		b.WriteString("Configured Argos profiles (none is active, so every model serves its own default command):\n\n")
	} else {
		fmt.Fprintf(&b, "Configured Argos profiles (active: %s):\n\n", active)
	}
	for _, profile := range profiles {
		marker := ""
		if profile.Name == active {
			marker = " [active]"
		}
		if profile.Description != "" {
			fmt.Fprintf(&b, "- %s%s: %s\n", profile.Name, marker, profile.Description)
			continue
		}
		fmt.Fprintf(&b, "- %s%s\n", profile.Name, marker)
	}
	return mcptools.CapResult(b.String(), mcptools.MaxToolResultBytes)
}

func (p *Provider) setActiveProfile(args map[string]json.RawMessage) mcptools.Result {
	// name is required by the schema, but a model can still omit it, and the
	// difference between "omitted" and "clear the selection" matters, so the
	// omitted case is answered explicitly instead of clearing by accident.
	if _, present := args["name"]; !present {
		return mcptools.Result{IsError: true, Content: "Error: set_active_profile needs a \"name\". Pass a " +
			"profile name from list_profiles, or an empty string to clear the selection."}
	}
	name := strings.TrimSpace(mcptools.StringArg(args, "name"))

	if err := p.state.SetActiveProfile(name); err != nil {
		return mcptools.Result{IsError: true, Content: fmt.Sprintf(
			"Error: %v. The active profile is unchanged (%s). Use list_profiles for the names that exist.",
			err, describeActive(p.state.ActiveProfile()))}
	}

	if name == "" {
		return mcptools.Result{Content: "Cleared the active profile. Every model now serves its own default " +
			"launch command. Models already loaded keep the command they were started with until they " +
			"unload."}
	}
	return mcptools.Result{Content: fmt.Sprintf(
		"Activated profile %q. Model launches that start from now on use it; models already loaded keep the "+
			"command they were started with until they unload or their TTL expires.", name)}
}

func (p *Provider) listModels() mcptools.Result {
	models := p.state.Models()
	if len(models) == 0 {
		return mcptools.Result{Content: "No models are configured in Argos. Add a model to the \"models\" " +
			"section of the configuration, or rescan the models directory."}
	}

	var b strings.Builder
	fmt.Fprintf(&b, "Configured Argos models (%d):\n\n", len(models))
	for _, m := range models {
		state := "unloaded"
		if m.Running {
			state = "loaded"
		}
		label := m.ID
		if m.Name != "" && m.Name != m.ID {
			label = fmt.Sprintf("%s (%s)", m.ID, m.Name)
		}
		fmt.Fprintf(&b, "- %s: %s", label, state)
		if m.TTL > 0 {
			fmt.Fprintf(&b, ", ttl %ds", m.TTL)
		}
		if m.Proxy != "" {
			fmt.Fprintf(&b, ", proxy %s", m.Proxy)
		}
		if m.Description != "" {
			fmt.Fprintf(&b, " -- %s", oneLine(m.Description))
		}
		b.WriteString("\n")
	}
	return mcptools.CapResult(b.String(), mcptools.MaxToolResultBytes)
}

func (p *Provider) runningModels() mcptools.Result {
	var running []ModelInfo
	for _, m := range p.state.Models() {
		if m.Running {
			running = append(running, m)
		}
	}
	sort.Slice(running, func(i, j int) bool { return running[i].ID < running[j].ID })

	if len(running) == 0 {
		return mcptools.Result{Content: "No models are loaded right now. Argos starts a model on its first " +
			"request, so this is the normal idle state."}
	}

	var b strings.Builder
	fmt.Fprintf(&b, "Models loaded right now (%d):\n\n", len(running))
	for _, m := range running {
		fmt.Fprintf(&b, "- %s", m.ID)
		if m.Proxy != "" {
			fmt.Fprintf(&b, ": %s", m.Proxy)
		}
		if m.TTL > 0 {
			fmt.Fprintf(&b, " (ttl %ds)", m.TTL)
		}
		b.WriteString("\n")
	}
	return mcptools.CapResult(b.String(), mcptools.MaxToolResultBytes)
}

func (p *Provider) unloadModels() mcptools.Result {
	before := 0
	for _, m := range p.state.Models() {
		if m.Running {
			before++
		}
	}
	p.state.UnloadAll()
	if before == 0 {
		return mcptools.Result{Content: "No models were loaded, so nothing was unloaded."}
	}
	return mcptools.Result{Content: fmt.Sprintf(
		"Unloaded %d model(s). They reload on their next request.", before)}
}

// describeActive renders the current selection for an error message, where
// "no profile" has to read as a state rather than as an empty string.
func describeActive(active string) string {
	if active == "" {
		return "no profile"
	}
	return fmt.Sprintf("profile %q", active)
}

// oneLine collapses whitespace so a multi-line description cannot turn one
// model's entry into several lines of the listing.
func oneLine(s string) string {
	return strings.Join(strings.Fields(s), " ")
}
