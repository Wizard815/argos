package odysseus

import "testing"

// A profile variant is the same model launched with different flags, so it has
// to inherit the base model's display name: without it a profile swap reads as
// a different model in the activity table and the model's log panel.
func TestBuild_VariantInheritsTheBaseModelName(t *testing.T) {
	st := State{
		Presets: []Preset{{
			Name:  "Qwen3.6-35B-A3B-MTP",
			Label: "mtp",
			Cmd:   cmdMTP,
		}},
	}

	res, err := Build(st, Options{
		EnsureDio: true,
		Names:     map[string]string{"qwen3.6-35b-a3b-ud-q6_k_xl": "Qwen3.6 35B"},
	})
	if err != nil {
		t.Fatalf("Build: %v", err)
	}

	const id = "qwen3.6-35b-a3b-ud-q6_k_xl--mtp"
	entry, ok := res.Models[id]
	if !ok {
		t.Fatalf("variant %q missing from the rendered models: %v", id, res.Models)
	}
	if got, want := entry["name"], "Qwen3.6 35B"; got != want {
		t.Errorf("variant name = %v, want the base model's name %q", got, want)
	}
}

// A base model with no configured name leaves the variant unnamed, so the UI
// keeps showing the id instead of inventing a label.
func TestBuild_VariantWithoutABaseNameStaysUnnamed(t *testing.T) {
	st := State{Presets: []Preset{{Label: "mtp", Cmd: cmdMTP}}}

	res, err := Build(st, Options{EnsureDio: true})
	if err != nil {
		t.Fatalf("Build: %v", err)
	}

	const id = "qwen3.6-35b-a3b-ud-q6_k_xl--mtp"
	entry, ok := res.Models[id]
	if !ok {
		t.Fatalf("variant %q missing from the rendered models: %v", id, res.Models)
	}
	if _, present := entry["name"]; present {
		t.Error("a variant must not invent a name when the base model has none")
	}
}

// Listed, a variant has to stand apart from its base in a client's model
// picker (Hermes, Open WebUI), so the profile's label goes in its name.
func TestBuild_ListedVariantNamesItsProfile(t *testing.T) {
	st := State{Presets: []Preset{{Label: "mtp", Cmd: cmdMTP}}}
	const id = "qwen3.6-35b-a3b-ud-q6_k_xl--mtp"

	for _, tc := range []struct {
		names map[string]string
		want  string
	}{
		{map[string]string{"qwen3.6-35b-a3b-ud-q6_k_xl": "Qwen3.6 35B"}, "Qwen3.6 35B (mtp)"},
		{nil, "qwen3.6-35b-a3b-ud-q6_k_xl (mtp)"},
	} {
		res, err := Build(st, Options{EnsureDio: true, ListVariants: true, Names: tc.names})
		if err != nil {
			t.Fatalf("Build: %v", err)
		}
		entry := res.Models[id]
		if entry["unlisted"] != false {
			t.Errorf("listed variant unlisted = %v, want false", entry["unlisted"])
		}
		if entry["name"] != tc.want {
			t.Errorf("listed variant name = %v, want %q", entry["name"], tc.want)
		}
	}

	res, err := Build(st, Options{EnsureDio: true})
	if err != nil {
		t.Fatalf("Build: %v", err)
	}
	if res.Models[id]["unlisted"] != true {
		t.Error("a variant must stay unlisted when ListVariants is off")
	}
}
