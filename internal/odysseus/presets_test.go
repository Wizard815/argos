package odysseus

import (
	"strings"
	"testing"
)

// TestOdysseus_BuildFromPresets covers the durable source: the Cookbook's saved
// launch configs. A task only holds a command while it is tracked -- once
// Odysseus stops it the entry moves to removedTasks, which keeps only an id and
// a timestamp -- so presets are what survive, and they carry the user's own
// label, which becomes the profile name.
func TestOdysseus_BuildFromPresets(t *testing.T) {
	second := strings.Replace(cmdTurbo4, "-c 4096", "-c 8192", 1)

	res, err := Build(State{Presets: []Preset{
		{Name: "Qwen3.8-27B", Model: "unsloth/Qwen3.8-27B-GGUF", Label: "fast", Cmd: cmdTurbo4},
		{Name: "Qwen3.6-35B", Model: "unsloth/Qwen3.6-35B-A3B-MTP-GGUF", Label: "fast", Cmd: cmdMTP},
		{Name: "Qwen3.8-27B", Model: "unsloth/Qwen3.8-27B-GGUF", Label: "long ctx", Cmd: second},
	}}, Options{ProfilePrefix: "ody", EnsureDio: true})
	if err != nil {
		t.Fatalf("Build: %v", err)
	}

	// one profile per label, shared across every model that has that label
	fast, ok := res.Profiles["ody-fast"]
	if !ok {
		t.Fatalf("profile ody-fast missing; have %v", keys(res.Profiles))
	}
	if pins := fast["pins"].(map[string]any); len(pins) != 2 {
		t.Errorf("ody-fast has %d pins, want 2 (one per model): %v", len(pins), pins)
	}

	// the label is sanitised for use as a profile name
	if _, ok := res.Profiles["ody-long-ctx"]; !ok {
		t.Errorf("label 'long ctx' should become ody-long-ctx; have %v", keys(res.Profiles))
	}

	if len(res.Models) != 3 {
		t.Errorf("got %d variant models, want 3: %v", len(res.Models), keys(res.Models))
	}
	for id, m := range res.Models {
		if !strings.Contains(id, "--ody-") {
			t.Errorf("variant %q is not namespaced under the prefix", id)
		}
		if !strings.Contains(m["cmd"].(string), "${PORT}") {
			t.Errorf("variant %q lost the port macro: %s", id, m["cmd"])
		}
		if len(m["env"].([]string)) == 0 {
			t.Errorf("variant %q has no env; the preset command's exports were dropped", id)
		}
	}
}

// TestOdysseus_PresetBeatsTaskForTheSameCommand keeps one variant, not two, when
// a preset and a still-tracked task describe the identical command.
func TestOdysseus_PresetBeatsTaskForTheSameCommand(t *testing.T) {
	state := State{
		Presets: []Preset{{Name: "m", Model: "unsloth/Qwen3.8-27B-GGUF", Label: "fast", Cmd: cmdTurbo4}},
		Tasks:   []Task{task("live", "running", 5000, "unsloth/Qwen3.8-27B-GGUF", cmdTurbo4)},
	}
	res, err := Build(state, Options{ProfilePrefix: "ody", EnsureDio: true})
	if err != nil {
		t.Fatal(err)
	}
	if len(res.Models) != 1 {
		t.Errorf("got %d variants, want 1 -- the preset and the task are the same command: %v",
			len(res.Models), keys(res.Models))
	}
	if _, ok := res.Profiles["ody-fast"]; !ok {
		t.Errorf("the preset's name should win; have %v", keys(res.Profiles))
	}
	if _, ok := res.Profiles["ody"]; ok {
		t.Errorf("a bare-prefix profile should not have been created too")
	}
}

// TestOdysseus_SanitizeProfileLabel pins the name mapping, since profile names
// are visible in the UI and collide across models.
func TestOdysseus_SanitizeProfileLabel(t *testing.T) {
	for in, want := range map[string]string{
		"fast":        "fast",
		"LoRA 8-bit":  "lora-8-bit",
		"  spaced  ":  "spaced",
		"a/b\\c":      "a-b-c",
		"":            "",
		"UPPER.Case_": "upper.case_",
	} {
		if got := SanitizeProfileLabel(in); got != want {
			t.Errorf("SanitizeProfileLabel(%q) = %q, want %q", in, got, want)
		}
	}
}

// TestOdysseus_BuildPresetSkipsNonLlamaCpp reports rather than guesses when a
// preset is not a llama.cpp launch.
func TestOdysseus_BuildPresetSkipsNonLlamaCpp(t *testing.T) {
	res, err := Build(State{Presets: []Preset{
		{Label: "vllm", Model: "org/m", Cmd: "python -m vllm.entrypoints.openai.api_server --model org/m"},
	}}, Options{ProfilePrefix: "ody", EnsureDio: true})
	if err != nil {
		t.Fatal(err)
	}
	if len(res.Models) != 0 || len(res.Profiles) != 0 {
		t.Errorf("a non-llama.cpp preset produced output: %v %v", keys(res.Models), keys(res.Profiles))
	}
	if len(res.Warnings) == 0 || !strings.Contains(strings.Join(res.Warnings, " "), "vllm") {
		t.Errorf("expected a warning naming the preset, got %v", res.Warnings)
	}
}
