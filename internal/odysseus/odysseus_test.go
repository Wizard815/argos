package odysseus

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/mostlygeek/llama-swap/internal/config"
)

// The three commands below are the real ones from the user's Odysseus UI.
// They are the whole point of the package, so they are tested as-is rather than
// as prettified fixtures.
const (
	cmdMTP = `export HIP_VISIBLE_DEVICES=0
export GGML_ENABLE_CUSTOM_AR=1
export HSA_FORCE_FINE_GRAIN_PCIE=1
export LLAMA_ENABLE_MTP_OPT=1
llama-server \
--model "$(printf %s '/app/models/HFCache/hub/models--unsloth--Qwen3.6-35B-A3B-MTP-GGUF/snapshots/5bc3e238d916f48a861bac2f8a1990a0e9b7e98d/Qwen3.6-35B-A3B-UD-Q6_K_XL.gguf')" \
--host 0.0.0.0 \
--port 8000 -ngl 99 -c 262144 \
--flash-attn on \
--cache-type-k q8_0 \
--cache-type-v q8_0 \
--fit off \
--split-mode layer \
--spec-type draft-mtp \
--spec-draft-n-max 3 \
--jinja`

	cmdTurbo4 = `export HIP_VISIBLE_DEVICES=0
llama-server \
--model "$(printf %s '/app/models/HFCache/hub/models--unsloth--Qwen3.8-27B-GGUF/snapshots/4ca720788d1e01f1bff70c033e0d0028fd02e502/Qwen3.8-27B-UD-Q6_K_XL.gguf')" \
--host 0.0.0.0 \
--port 8000 -ngl 99 -c 4096 \
--flash-attn on \
--cache-type-k turbo4 \
--cache-type-v turbo4 \
--fit off \
--split-mode layer`

	cmdVision = `export HIP_VISIBLE_DEVICES=0
llama-server \
--model "$(printf %s '/app/models/Qwen3VL-30B-A3B-Thinking-Q4_K_M/Qwen3VL-30B-A3B-Thinking-Q4_K_M.gguf')" \
--host 0.0.0.0 \
--port 8000 -ngl 99 -c 262144 \
--flash-attn on \
--cache-type-k q8_0 \
--cache-type-v q8_0 \
--fit off \
--split-mode layer \
--spec-type draft-mtp \
--spec-draft-n-max 3 \
--mmproj "$(printf %s '/app/models/Qwen3VL-30B-A3B-Thinking-Q4_K_M/mmproj-Qwen3VL-30B-A3B-Thinking-F16.gguf')" \
--image-max-tokens 1024`
)

// task builds a Task the way cookbook_state.json stores one.
func task(id, status string, ts int64, repo, cmd string) Task {
	return Task{ID: id, Status: status, TS: ts, Payload: Payload{RepoID: repo, Cmd: cmd}}
}

// TestParseCommand_RealCommands covers the translation this package exists for:
// export lines to env, command substitution unwrapped, port handed to ${PORT},
// and -lm dio injected because FEATURES.md calls it required.
func TestParseCommand_RealCommands(t *testing.T) {
	tests := []struct {
		name        string
		cmd         string
		wantID      string
		wantEnv     []string
		wantMMProj  string
		wantSpecMTP bool
	}{
		{
			name:        "mtp qwen3.6 35b a3b",
			cmd:         cmdMTP,
			wantID:      "qwen3.6-35b-a3b-ud-q6_k_xl",
			wantEnv:     []string{"HIP_VISIBLE_DEVICES=0", "GGML_ENABLE_CUSTOM_AR=1", "HSA_FORCE_FINE_GRAIN_PCIE=1", "LLAMA_ENABLE_MTP_OPT=1"},
			wantSpecMTP: true,
		},
		{
			name:    "qwen3.8 27b turbo4",
			cmd:     cmdTurbo4,
			wantID:  "qwen3.8-27b-ud-q6_k_xl",
			wantEnv: []string{"HIP_VISIBLE_DEVICES=0"},
		},
		{
			name:        "qwen3vl 30b vision",
			cmd:         cmdVision,
			wantID:      "qwen3vl-30b-a3b-thinking-q4_k_m",
			wantEnv:     []string{"HIP_VISIBLE_DEVICES=0"},
			wantMMProj:  "/app/models/Qwen3VL-30B-A3B-Thinking-Q4_K_M/mmproj-Qwen3VL-30B-A3B-Thinking-F16.gguf",
			wantSpecMTP: true,
		},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			p, err := ParseCommand(tc.cmd, true)
			if err != nil {
				t.Fatalf("ParseCommand: %v", err)
			}
			// the whole point: the derived ID must match what modelscan already
			// generated for the same file
			if p.ModelID != tc.wantID {
				t.Errorf("ModelID = %q, want %q", p.ModelID, tc.wantID)
			}
			if p.MMProjPath != tc.wantMMProj {
				t.Errorf("MMProjPath = %q, want %q", p.MMProjPath, tc.wantMMProj)
			}
			joined := strings.Join(p.Env, ",")
			for _, want := range tc.wantEnv {
				if !strings.Contains(joined, want) {
					t.Errorf("env %q missing from %q", want, joined)
				}
			}
			if strings.Contains(strings.Join(p.Argv, " "), "export ") {
				t.Error("an `export` token survived into argv -- llama-swap would try to exec it")
			}
			if strings.Contains(strings.Join(p.Argv, " "), "$(") {
				t.Error("command substitution survived -- llama-swap has no shell")
			}
			if indexOf(p.Argv, "--port") < 0 || indexOf(p.Argv, "${PORT}") < 0 {
				t.Errorf("port was not handed to ${PORT}: %v", p.Argv)
			}
			if indexOf(p.Argv, "-lm") < 0 {
				t.Errorf("-lm dio was not injected: %v", p.Argv)
			}
			if indexOf(p.Argv, "dio") < 0 {
				t.Errorf("-lm without dio: %v", p.Argv)
			}
			hasMTP := indexOf(p.Argv, "--spec-type") >= 0
			if hasMTP != tc.wantSpecMTP {
				t.Errorf("--spec-type present = %v, want %v", hasMTP, tc.wantSpecMTP)
			}
			if tc.wantID == "qwen3vl-30b-a3b-thinking-q4_k_m" && indexOf(p.Argv, "--mmproj") < 0 {
				t.Error("--mmproj was dropped")
			}
		})
	}
}

// TestParseCommand_NoDioWhenDisabled guards the opt-out.
func TestParseCommand_NoDioWhenDisabled(t *testing.T) {
	p, err := ParseCommand(cmdTurbo4, false)
	if err != nil {
		t.Fatalf("ParseCommand: %v", err)
	}
	if indexOf(p.Argv, "-lm") >= 0 {
		t.Errorf("-lm injected despite ensureDio=false: %v", p.Argv)
	}
}

// TestParseCommand_KeepsExistingLoadMode makes sure injection does not double up.
func TestParseCommand_KeepsExistingLoadMode(t *testing.T) {
	p, err := ParseCommand("llama-server -m /app/models/m.gguf --port 8000 -lm dio", true)
	if err != nil {
		t.Fatalf("ParseCommand: %v", err)
	}
	n := 0
	for _, a := range p.Argv {
		if a == "-lm" || a == "--load-mode" {
			n++
		}
	}
	if n != 1 {
		t.Errorf("found %d load-mode flags, want 1: %v", n, p.Argv)
	}
}

// TestParseCommand_RejectsNonGGUF ensures a vLLM-style task is skipped rather
// than mapped onto a wrong model.
func TestParseCommand_RejectsNonGGUF(t *testing.T) {
	if _, err := ParseCommand("python -m vllm.entrypoints.openai.api_server --model org/m --port 8000", true); err == nil {
		t.Fatal("expected an error for a command with no .gguf path")
	}
}

// TestBuild_GroupsTasksIntoProfiles checks the variant + profile shape, and that
// the latest task per model becomes the bare prefix.
func TestBuild_GroupsTasksIntoProfiles(t *testing.T) {
	state := State{Tasks: []Task{
		task("old", "stopped", 1000, "unsloth/Qwen3.6-35B-A3B-MTP-GGUF", cmdMTP),
		task("new", "running", 2000, "unsloth/Qwen3.8-27B-GGUF", cmdTurbo4),
		task("vllm", "stopped", 1500, "org/m", "python -m vllm.entrypoints.openai.api_server --model org/m"),
	}}

	res, err := Build(state, Options{ProfilePrefix: "ody", EnsureDio: true})
	if err != nil {
		t.Fatalf("Build: %v", err)
	}
	if len(res.Models) != 2 {
		t.Fatalf("got %d variant models, want 2: %v", len(res.Models), keys(res.Models))
	}
	// Both models contribute to ONE profile named after the prefix. That is the
	// intended shape: a profile is a mode applied across every model that has a
	// command, which is what llama-swap's single-active-profile rule rewards.
	// A model's second distinct command would land in "<prefix>-2".
	if len(res.Profiles) != 1 {
		t.Fatalf("got %d profiles, want 1: %v", len(res.Profiles), keys(res.Profiles))
	}
	pins := res.Profiles["ody"]["pins"].(map[string]any)
	if len(pins) != 2 {
		t.Errorf("profile 'ody' has %d pins, want 2: %v", len(pins), pins)
	}
	for want := range map[string]bool{
		"qwen3.6-35b-a3b-ud-q6_k_xl--ody": true,
		"qwen3.8-27b-ud-q6_k_xl--ody":     true,
	} {
		if _, ok := res.Models[want]; !ok {
			t.Errorf("variant %q missing; have %v", want, keys(res.Models))
		}
	}
	// the vLLM task must be reported, not silently mapped
	found := false
	for _, w := range res.Warnings {
		if strings.Contains(w, "vllm") {
			found = true
		}
	}
	if !found {
		t.Errorf("expected a warning about the vLLM task, got %v", res.Warnings)
	}
}

// TestBuild_SecondCommandBecomesODy2 covers the naming of a model's older
// commands: newest wins "<prefix>", older distinct ones become "<prefix>-N".
func TestBuild_SecondCommandBecomesODy2(t *testing.T) {
	older := strings.Replace(cmdTurbo4, "-c 4096", "-c 8192", 1)
	res, err := Build(State{Tasks: []Task{
		task("older", "stopped", 1000, "unsloth/Qwen3.8-27B-GGUF", older),
		task("newer", "running", 2000, "unsloth/Qwen3.8-27B-GGUF", cmdTurbo4),
	}}, Options{ProfilePrefix: "ody", EnsureDio: true})
	if err != nil {
		t.Fatal(err)
	}
	if _, ok := res.Profiles["ody"]; !ok {
		t.Errorf("newest command should own the bare prefix; have %v", keys(res.Profiles))
	}
	if _, ok := res.Profiles["ody-2"]; !ok {
		t.Errorf("older command should own <prefix>-2; have %v", keys(res.Profiles))
	}
	// the newest command must be the one pinned by the bare prefix
	pins := res.Profiles["ody"]["pins"].(map[string]any)
	pinned := pins["qwen3.8-27b-ud-q6_k_xl"].(string)
	if strings.Contains(res.Models[pinned]["cmd"].(string), "-c 8192") {
		t.Errorf("bare prefix pinned the older command: %s", pinned)
	}
}

// TestBuild_SkipsDisallowedStatuses keeps a deliberately stopped task out when
// the operator narrows the statuses.
func TestBuild_SkipsDisallowedStatuses(t *testing.T) {
	res, err := Build(State{Tasks: []Task{
		task("a", "stopped", 1000, "unsloth/Qwen3.8-27B-GGUF", cmdTurbo4),
	}}, Options{ProfilePrefix: "ody", Statuses: []string{"running"}})
	if err != nil {
		t.Fatal(err)
	}
	if len(res.Models) != 0 || len(res.Profiles) != 0 {
		t.Errorf("stopped task was included despite statuses=[running]: %v", keys(res.Models))
	}
}

// TestGeneratedFragmentLoadsAsConfig is the real proof: render the fragment and
// push it through llama-swap's own config loader, with a base config that
// already defines the base models -- exercising the -config-dir merge and its
// identity-map duplicate detection rather than a hand-rolled parse.
func TestGeneratedFragmentLoadsAsConfig(t *testing.T) {
	res, err := Build(State{Tasks: []Task{
		task("a", "running", 2000, "unsloth/Qwen3.6-35B-A3B-MTP-GGUF", cmdMTP),
		task("b", "running", 2001, "unsloth/Qwen3VL-30B-A3B", cmdVision),
	}}, Options{ProfilePrefix: "ody", EnsureDio: true})
	if err != nil {
		t.Fatalf("Build: %v", err)
	}
	frag, err := Render(res)
	if err != nil {
		t.Fatalf("Render: %v", err)
	}

	dir := t.TempDir()
	confDir := filepath.Join(dir, "config.d")
	if err := os.MkdirAll(confDir, 0o755); err != nil {
		t.Fatal(err)
	}
	base := `healthCheckTimeout: 30
models:
  qwen3.6-35b-a3b-ud-q6_k_xl:
    cmd: llama-server -m /app/models/a.gguf --port ${PORT} -lm dio
  qwen3vl-30b-a3b-thinking-q4_k_m:
    cmd: llama-server -m /app/models/b.gguf --port ${PORT} -lm dio
`
	confPath := filepath.Join(dir, "config.yaml")
	if err := os.WriteFile(confPath, []byte(base), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(confDir, "odysseus.generated.yaml"), frag, 0o644); err != nil {
		t.Fatal(err)
	}

	cfg, err := config.LoadConfigSources(confPath, confDir)
	if err != nil {
		t.Fatalf("llama-swap rejected the generated fragment: %v\n--- fragment ---\n%s", err, frag)
	}

	if _, ok := cfg.Models["qwen3.6-35b-a3b-ud-q6_k_xl"]; !ok {
		t.Error("base model vanished from the merged config")
	}
	if _, ok := cfg.Models["qwen3.6-35b-a3b-ud-q6_k_xl--ody"]; !ok {
		t.Errorf("variant model missing from merged config: %v", keys(cfg.Models))
	}
	prof, ok := cfg.Profiles["ody"]
	if !ok {
		t.Fatalf("profile 'ody' missing; have %v", keys(cfg.Profiles))
	}
	if prof.Pins["qwen3.6-35b-a3b-ud-q6_k_xl"] != "qwen3.6-35b-a3b-ud-q6_k_xl--ody" {
		t.Errorf("pin not rewritten: %v", prof.Pins)
	}
	// the multimodal command must keep its projector through the round trip
	vc := cfg.Models["qwen3vl-30b-a3b-thinking-q4_k_m--ody"]
	if !strings.Contains(vc.Cmd, "--mmproj") {
		t.Errorf("variant cmd lost --mmproj: %q", vc.Cmd)
	}
	// and the env must survive, since that is what pins the GPU
	if len(vc.Env) == 0 || vc.Env[0] != "HIP_VISIBLE_DEVICES=0" {
		t.Errorf("variant env = %v, want HIP_VISIBLE_DEVICES=0", vc.Env)
	}
}

// TestRenderOmitsEmptySections keeps the fragment from writing `models: {}`
// and thereby clobbering the map when nothing was derived.
func TestRenderOmitsEmptySections(t *testing.T) {
	frag, err := Render(&Result{
		Models:   map[string]map[string]any{},
		Profiles: map[string]map[string]any{},
	})
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(frag), "models:") || strings.Contains(string(frag), "profiles:") {
		t.Errorf("empty sections were emitted:\n%s", frag)
	}
}

func keys[V any](m map[string]V) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	return out
}
