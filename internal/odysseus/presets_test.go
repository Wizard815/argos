package odysseus

import (
	"strings"
	"testing"
)

// TestOdysseus_BuildFromPresets covers variant naming: the Odysseus label is
// used verbatim (sanitised) as the variant suffix, so a config saved as "fast"
// becomes "<base>--fast".
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

	// no global profile without choices -- that is the whole point
	if len(res.Profiles) != 0 || res.ProfileName != "" {
		t.Errorf("profiles generated without choices: %v (name %q)", keys(res.Profiles), res.ProfileName)
	}

	if len(res.Models) != 3 {
		t.Fatalf("got %d variant models, want 3: %v", len(res.Models), keys(res.Models))
	}
	for _, want := range []string{
		"qwen3.8-27b-ud-q6_k_xl--fast",
		"qwen3.6-35b-a3b-ud-q6_k_xl--fast",
		"qwen3.8-27b-ud-q6_k_xl--long-ctx",
	} {
		if _, ok := res.Models[want]; !ok {
			t.Errorf("variant %q missing; have %v", want, keys(res.Models))
		}
	}
	for id, m := range res.Models {
		if !strings.Contains(m["cmd"].(string), "${PORT}") {
			t.Errorf("variant %q lost the port macro: %s", id, m["cmd"])
		}
		if len(m["env"].([]string)) == 0 {
			t.Errorf("variant %q has no env; the preset command's exports were dropped", id)
		}
	}
}

// TestOdysseus_ChoicesBuildOneUnionProfile is the core of the per-model design:
// choosing a preset for SOME models pins only those models. A model without a
// choice gets no pin, so it keeps llama-swap's own launch and nothing carries
// over between models.
func TestOdysseus_ChoicesBuildOneUnionProfile(t *testing.T) {
	res, err := Build(State{Presets: []Preset{
		{Name: "Qwen3.8-27B", Model: "unsloth/Qwen3.8-27B-GGUF", Label: "fast", Cmd: cmdTurbo4},
		{Name: "Qwen3.6-35B", Model: "unsloth/Qwen3.6-35B-A3B-MTP-GGUF", Label: "fast", Cmd: cmdMTP},
	}}, Options{
		ProfilePrefix: "ody",
		EnsureDio:     true,
		Choices: map[string]string{
			"qwen3.8-27b-ud-q6_k_xl": "fast", // chosen
			// the 35B deliberately has no choice
		},
	})
	if err != nil {
		t.Fatalf("Build: %v", err)
	}

	if res.ProfileName != "ody" {
		t.Fatalf("ProfileName = %q, want %q", res.ProfileName, "ody")
	}
	prof, ok := res.Profiles["ody"]
	if !ok {
		t.Fatalf("union profile missing; have %v", keys(res.Profiles))
	}
	pins := prof["pins"].(map[string]any)
	if len(pins) != 1 {
		t.Fatalf("union profile has %d pins, want exactly the 1 chosen model: %v", len(pins), pins)
	}
	if pins["qwen3.8-27b-ud-q6_k_xl"] != "qwen3.8-27b-ud-q6_k_xl--fast" {
		t.Errorf("pin points at %v, want the --fast variant", pins)
	}
	if _, carried := pins["qwen3.6-35b-a3b-ud-q6_k_xl"]; carried {
		t.Error("the unchosen model was pinned -- it must keep llama-swap's own launch")
	}
	if res.ChoicesResolved["qwen3.8-27b-ud-q6_k_xl"] != "qwen3.8-27b-ud-q6_k_xl--fast" {
		t.Errorf("ChoicesResolved = %v", res.ChoicesResolved)
	}
}

// TestOdysseus_ChoiceForUnknownLabelWarns covers picking a label the model has
// no saved config for: warn and skip, never emit a dangling pin.
func TestOdysseus_ChoiceForUnknownLabelWarns(t *testing.T) {
	res, err := Build(State{Presets: []Preset{
		{Label: "fast", Model: "unsloth/Qwen3.8-27B-GGUF", Cmd: cmdTurbo4},
	}}, Options{
		ProfilePrefix: "ody",
		EnsureDio:     true,
		Choices:       map[string]string{"qwen3.8-27b-ud-q6_k_xl": "no-such-label"},
	})
	if err != nil {
		t.Fatal(err)
	}
	if len(res.Profiles) != 0 || res.ProfileName != "" {
		t.Errorf("a dangling choice generated a profile: %v (name %q)", keys(res.Profiles), res.ProfileName)
	}
	joined := strings.Join(res.Warnings, "\n")
	if !strings.Contains(joined, "no-such-label") {
		t.Errorf("warning does not name the bad label: %v", res.Warnings)
	}
}

// TestOdysseus_ChoicePersistence round-trips the choice file.
func TestOdysseus_ChoicePersistence(t *testing.T) {
	path := t.TempDir() + "/odysseus-choices.json"

	// a missing file is not an error -- it means nothing chosen yet
	cs, err := LoadChoices(path)
	if err != nil {
		t.Fatalf("LoadChoices on a missing file: %v", err)
	}
	if len(cs.Choices) != 0 {
		t.Errorf("expected empty choices, got %v", cs.Choices)
	}

	cs.Choices["model-a"] = "fast"
	cs.Choices["model-b"] = "long ctx"
	if err := SaveChoices(path, cs); err != nil {
		t.Fatalf("SaveChoices: %v", err)
	}

	back, err := LoadChoices(path)
	if err != nil {
		t.Fatal(err)
	}
	if back.Choices["model-a"] != "fast" || back.Choices["model-b"] != "long ctx" {
		t.Errorf("round-trip lost data: %v", back.Choices)
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
	if len(res.Models) != 0 {
		t.Errorf("a non-llama.cpp preset produced variants: %v", keys(res.Models))
	}
	if len(res.Warnings) == 0 || !strings.Contains(strings.Join(res.Warnings, " "), "vllm") {
		t.Errorf("expected a warning naming the preset, got %v", res.Warnings)
	}
}
