package modelctl

import (
	"context"
	"encoding/json"
	"errors"
	"strings"
	"testing"

	"github.com/Wizard815/Argos/internal/mcptools"
)

// fakeState stands in for the running server. It records the writes so a test
// can prove a tool actually reached the server rather than only returning a
// plausible sentence about it.
type fakeState struct {
	profiles []ProfileInfo
	active   string
	models   []ModelInfo

	setErr    error
	setCalls  []string
	unloads   int
	modelCall int
}

func (f *fakeState) Profiles() []ProfileInfo { return f.profiles }

func (f *fakeState) ActiveProfile() string { return f.active }

func (f *fakeState) SetActiveProfile(name string) error {
	f.setCalls = append(f.setCalls, name)
	if f.setErr != nil {
		return f.setErr
	}
	f.active = name
	return nil
}

func (f *fakeState) Models() []ModelInfo {
	f.modelCall++
	return f.models
}

func (f *fakeState) UnloadAll() {
	f.unloads++
	for i := range f.models {
		f.models[i].Running = false
	}
}

func call(t *testing.T, p *Provider, name string, args map[string]any) mcptools.Result {
	t.Helper()
	raw := map[string]json.RawMessage{}
	for key, value := range args {
		encoded, err := json.Marshal(value)
		if err != nil {
			t.Fatalf("marshalling %s: %v", key, err)
		}
		raw[key] = encoded
	}
	result, err := p.Call(context.Background(), name, raw)
	if err != nil {
		t.Fatalf("Call(%s) returned a protocol error: %v", name, err)
	}
	return result
}

func TestToolsExposeModelAndProfileSurface(t *testing.T) {
	tools, err := NewProvider(&fakeState{}).Tools(context.Background())
	if err != nil {
		t.Fatalf("Tools: %v", err)
	}

	want := []string{"list_profiles", "set_active_profile", "list_models", "running_models", "unload_models"}
	if len(tools) != len(want) {
		t.Fatalf("got %d tools, want %d", len(tools), len(want))
	}
	for i, name := range want {
		if tools[i].Name != name {
			t.Errorf("tool %d is %q, want %q", i, tools[i].Name, name)
		}
		if tools[i].Description == "" {
			t.Errorf("tool %q has no description", name)
		}
		if tools[i].InputSchema == nil {
			t.Errorf("tool %q has no input schema", name)
		}
	}

	// The two mutating tools must not claim to be read-only: a client decides
	// how much ceremony a call needs from this hint alone.
	for _, tool := range tools {
		if tool.Annotations == nil {
			t.Errorf("tool %q has no annotations", tool.Name)
			continue
		}
		writes := tool.Name == "set_active_profile" || tool.Name == "unload_models"
		if tool.Annotations.ReadOnlyHint == writes {
			t.Errorf("tool %q has ReadOnlyHint=%v, which is wrong for a %s tool",
				tool.Name, tool.Annotations.ReadOnlyHint, map[bool]string{true: "mutating", false: "reading"}[writes])
		}
	}
}

func TestListProfilesWithNoneConfigured(t *testing.T) {
	result := call(t, NewProvider(&fakeState{}), "list_profiles", nil)
	if result.IsError {
		t.Fatalf("unexpected tool error: %s", result.Content)
	}
	if !strings.Contains(result.Content, "No profiles are configured") {
		t.Errorf("content should say there are none, got: %s", result.Content)
	}
}

func TestListProfilesMarksTheActiveOne(t *testing.T) {
	state := &fakeState{
		profiles: []ProfileInfo{
			{Name: "coding", Description: "long context"},
			{Name: "fast"},
		},
		active: "coding",
	}
	result := call(t, NewProvider(state), "list_profiles", nil)
	if result.IsError {
		t.Fatalf("unexpected tool error: %s", result.Content)
	}
	for _, want := range []string{"coding [active]: long context", "fast"} {
		if !strings.Contains(result.Content, want) {
			t.Errorf("content should contain %q, got:\n%s", want, result.Content)
		}
	}
	// The inactive profile must not be marked active.
	if strings.Contains(result.Content, "fast [active]") {
		t.Errorf("only the active profile should carry the marker, got:\n%s", result.Content)
	}
	if !strings.Contains(result.Content, "active: coding") {
		t.Errorf("the header should name the active profile, got:\n%s", result.Content)
	}
}

func TestListProfilesWithNoActiveProfile(t *testing.T) {
	state := &fakeState{profiles: []ProfileInfo{{Name: "coding"}}}
	result := call(t, NewProvider(state), "list_profiles", nil)
	if !strings.Contains(result.Content, "none is active") {
		t.Errorf("content should say nothing is active, got:\n%s", result.Content)
	}
	if strings.Contains(result.Content, "[active]") {
		t.Errorf("no profile should be marked active, got:\n%s", result.Content)
	}
}

func TestSetActiveProfileRequiresTheNameArgument(t *testing.T) {
	state := &fakeState{profiles: []ProfileInfo{{Name: "coding"}}, active: "coding"}
	result := call(t, NewProvider(state), "set_active_profile", nil)
	if !result.IsError {
		t.Fatalf("omitting name should be a tool error, got: %s", result.Content)
	}
	// The distinction between "omitted" and "clear the selection" is the whole
	// reason this is an error: neither may happen by accident.
	if len(state.setCalls) != 0 {
		t.Errorf("no write should have reached the server, got %v", state.setCalls)
	}
}

func TestSetActiveProfileReportsAnUnknownName(t *testing.T) {
	state := &fakeState{
		profiles: []ProfileInfo{{Name: "coding"}},
		active:   "coding",
		setErr:   errors.New(`profile "nope" not found`),
	}
	result := call(t, NewProvider(state), "set_active_profile", map[string]any{"name": "nope"})
	if !result.IsError {
		t.Fatalf("an unknown profile should be a tool error, got: %s", result.Content)
	}
	if !strings.Contains(result.Content, "not found") {
		t.Errorf("the server's reason should survive into the result, got: %s", result.Content)
	}
	if !strings.Contains(result.Content, `profile "coding"`) {
		t.Errorf("the result should say what is still active, got: %s", result.Content)
	}
}

func TestSetActiveProfileActivatesAName(t *testing.T) {
	state := &fakeState{profiles: []ProfileInfo{{Name: "coding"}}, active: ""}
	result := call(t, NewProvider(state), "set_active_profile", map[string]any{"name": "coding"})
	if result.IsError {
		t.Fatalf("unexpected tool error: %s", result.Content)
	}
	if len(state.setCalls) != 1 || state.setCalls[0] != "coding" {
		t.Errorf("the write should have reached the server, got %v", state.setCalls)
	}
	if !strings.Contains(result.Content, `"coding"`) {
		t.Errorf("the result should name the profile, got: %s", result.Content)
	}
}

func TestSetActiveProfileClearsWithAnEmptyName(t *testing.T) {
	state := &fakeState{profiles: []ProfileInfo{{Name: "coding"}}, active: "coding"}
	result := call(t, NewProvider(state), "set_active_profile", map[string]any{"name": ""})
	if result.IsError {
		t.Fatalf("unexpected tool error: %s", result.Content)
	}
	if len(state.setCalls) != 1 || state.setCalls[0] != "" {
		t.Errorf("clearing should reach the server as an empty name, got %v", state.setCalls)
	}
	if !strings.Contains(result.Content, "Cleared") {
		t.Errorf("the result should say the selection was cleared, got: %s", result.Content)
	}
}

func TestSetActiveProfileRejectsAWhitespaceName(t *testing.T) {
	state := &fakeState{profiles: []ProfileInfo{{Name: "coding"}}, active: "coding"}
	result := call(t, NewProvider(state), "set_active_profile", map[string]any{"name": "   "})
	if result.IsError {
		t.Fatalf("a whitespace name trims to empty, which clears rather than fails: %s", result.Content)
	}
	if len(state.setCalls) != 1 || state.setCalls[0] != "" {
		t.Errorf("a whitespace name should reach the server trimmed, got %v", state.setCalls)
	}
}

func TestListModelsReportsStateTTLAndProxy(t *testing.T) {
	state := &fakeState{models: []ModelInfo{
		{ID: "qwen3-8b", Name: "Qwen3 8B", Running: true, TTL: 300, Proxy: "http://127.0.0.1:58001"},
		{ID: "big-70b", Description: "slow\nbut\nbig"},
	}}
	result := call(t, NewProvider(state), "list_models", nil)
	if result.IsError {
		t.Fatalf("unexpected tool error: %s", result.Content)
	}
	for _, want := range []string{
		"qwen3-8b (Qwen3 8B): loaded, ttl 300s, proxy http://127.0.0.1:58001",
		"big-70b: unloaded",
		"slow but big",
	} {
		if !strings.Contains(result.Content, want) {
			t.Errorf("content should contain %q, got:\n%s", want, result.Content)
		}
	}
	if strings.Contains(result.Content, "slow\nbut") {
		t.Errorf("a multi-line description must be collapsed, got:\n%s", result.Content)
	}
}

func TestListModelsWithNoneConfigured(t *testing.T) {
	result := call(t, NewProvider(&fakeState{}), "list_models", nil)
	if result.IsError {
		t.Fatalf("unexpected tool error: %s", result.Content)
	}
	if !strings.Contains(result.Content, "No models are configured") {
		t.Errorf("content should say there are none, got: %s", result.Content)
	}
}

func TestRunningModelsListsOnlyLoadedOnes(t *testing.T) {
	state := &fakeState{models: []ModelInfo{
		{ID: "loaded-b", Running: true, Proxy: "http://127.0.0.1:58002"},
		{ID: "idle"},
		{ID: "loaded-a", Running: true},
	}}
	result := call(t, NewProvider(state), "running_models", nil)
	if result.IsError {
		t.Fatalf("unexpected tool error: %s", result.Content)
	}
	if strings.Contains(result.Content, "idle") {
		t.Errorf("an unloaded model must not be listed as running, got:\n%s", result.Content)
	}
	if !strings.Contains(result.Content, "http://127.0.0.1:58002") {
		t.Errorf("the reachable address should be reported, got:\n%s", result.Content)
	}
	// Sorted, because a model reads the listing as text and a stable order
	// makes two calls comparable.
	if strings.Index(result.Content, "loaded-a") > strings.Index(result.Content, "loaded-b") {
		t.Errorf("entries should be sorted by id, got:\n%s", result.Content)
	}
}

func TestRunningModelsWhenNothingIsLoaded(t *testing.T) {
	result := call(t, NewProvider(&fakeState{models: []ModelInfo{{ID: "idle"}}}), "running_models", nil)
	if result.IsError {
		t.Fatalf("unexpected tool error: %s", result.Content)
	}
	if !strings.Contains(result.Content, "No models are loaded right now") {
		t.Errorf("content should say the server is idle, got: %s", result.Content)
	}
}

func TestUnloadModelsReportsHowManyItUnloaded(t *testing.T) {
	state := &fakeState{models: []ModelInfo{
		{ID: "a", Running: true},
		{ID: "b", Running: true},
		{ID: "c"},
	}}
	result := call(t, NewProvider(state), "unload_models", nil)
	if result.IsError {
		t.Fatalf("unexpected tool error: %s", result.Content)
	}
	if state.unloads != 1 {
		t.Errorf("the unload should have reached the server once, got %d", state.unloads)
	}
	if !strings.Contains(result.Content, "Unloaded 2 model") {
		t.Errorf("the count should be the number that were loaded, got: %s", result.Content)
	}
}

func TestUnloadModelsWithNothingLoaded(t *testing.T) {
	state := &fakeState{models: []ModelInfo{{ID: "a"}}}
	result := call(t, NewProvider(state), "unload_models", nil)
	if result.IsError {
		t.Fatalf("unexpected tool error: %s", result.Content)
	}
	if state.unloads != 1 {
		t.Errorf("the unload is still forwarded when nothing is loaded, got %d", state.unloads)
	}
	if !strings.Contains(result.Content, "nothing was unloaded") {
		t.Errorf("the result should say there was nothing to do, got: %s", result.Content)
	}
}

func TestUnknownToolIsAProtocolError(t *testing.T) {
	_, err := NewProvider(&fakeState{}).Call(context.Background(), "nope", nil)
	if err == nil {
		t.Fatal("an unknown tool must be a protocol error, not a tool result")
	}
}

func TestProviderIDIsTheModelsNamespace(t *testing.T) {
	if got := NewProvider(&fakeState{}).ID(); got != "models" {
		t.Errorf("provider id is %q, want %q", got, "models")
	}
}
