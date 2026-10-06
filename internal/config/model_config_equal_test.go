package config

import "testing"

func TestModelConfigEqual_IdenticalConfigsAreEqual(t *testing.T) {
	a := ModelConfig{Cmd: "llama-server --port 8080", Env: []string{"A=1"}, UnloadAfter: 0}
	b := ModelConfig{Cmd: "llama-server --port 8080", Env: []string{"A=1"}, UnloadAfter: 0}

	if !ModelConfigEqual(a, b) {
		t.Errorf("identical configurations should compare equal")
	}
}

func TestModelConfigEqual_ChangedCommandIsNotEqual(t *testing.T) {
	a := ModelConfig{Cmd: "llama-server --port 8080 -c 4096"}
	b := ModelConfig{Cmd: "llama-server --port 8080 -c 8192"}

	if ModelConfigEqual(a, b) {
		t.Errorf("a changed command line should compare not equal")
	}
}

func TestModelConfigEqual_ChangedEnvIsNotEqual(t *testing.T) {
	a := ModelConfig{Cmd: "x", Env: []string{"A=1"}}
	b := ModelConfig{Cmd: "x", Env: []string{"A=2"}}

	if ModelConfigEqual(a, b) {
		t.Errorf("a changed environment should compare not equal")
	}
}

// Map ordering must not decide this. The same metadata written in a different
// order is the same launch, and treating it as a change would restart a warm
// model - dropping its prompt cache - for nothing.
func TestModelConfigEqual_IgnoresMapOrdering(t *testing.T) {
	a := ModelConfig{Cmd: "x", Metadata: map[string]any{"one": 1, "two": 2, "three": 3}}
	b := ModelConfig{Cmd: "x", Metadata: map[string]any{"three": 3, "two": 2, "one": 1}}

	if !ModelConfigEqual(a, b) {
		t.Errorf("metadata ordering alone should not count as a change")
	}
}

// A configuration the comparison cannot serialise must report "not equal": the
// conservative direction is to restart the model rather than carry over a
// process whose launch may differ.
func TestModelConfigEqual_UnserialisableReportsNotEqual(t *testing.T) {
	a := ModelConfig{Cmd: "x", Metadata: map[string]any{"f": func() {}}}
	b := ModelConfig{Cmd: "x", Metadata: map[string]any{"f": func() {}}}

	if ModelConfigEqual(a, b) {
		t.Errorf("a config that cannot be serialised should compare not equal")
	}
}
