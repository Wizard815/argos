package odysseus

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// TestOdysseus_SourceKeysReadsIdentityMaps covers the duplicate guard's input:
// the model and profile keys another config source defines.
func TestOdysseus_SourceKeysReadsIdentityMaps(t *testing.T) {
	dir := t.TempDir()
	base := filepath.Join(dir, "config.yaml")
	write(t, base, `healthCheckTimeout: 30
models:
  qwen3.6-35b-a3b-ud-q6_k_xl:
    cmd: llama-server -m /app/models/a.gguf --port ${PORT}
profiles:
  coding:
    pins:
      qwen3.6-35b-a3b-ud-q6_k_xl: qwen3.6-35b-a3b-ud-q6_k_xl--ody
`)
	other := filepath.Join(dir, "models.generated.yaml")
	write(t, other, `models:
  gemma-4-12b-it-ud-q8_k_xl:
    cmd: llama-server -m /app/models/g.gguf --port ${PORT}
`)

	models, profiles, err := SourceKeys([]string{base, other}, "")
	if err != nil {
		t.Fatalf("SourceKeys: %v", err)
	}
	if _, ok := models["qwen3.6-35b-a3b-ud-q6_k_xl"]; !ok {
		t.Errorf("base model key not collected: %v", keys(models))
	}
	if _, ok := models["gemma-4-12b-it-ud-q8_k_xl"]; !ok {
		t.Errorf("second source's model key not collected: %v", keys(models))
	}
	if _, ok := profiles["coding"]; !ok {
		t.Errorf("profile key not collected: %v", keys(profiles))
	}
	if models["qwen3.6-35b-a3b-ud-q6_k_xl"] != base {
		t.Errorf("key attributed to %q, want %q", models["qwen3.6-35b-a3b-ud-q6_k_xl"], base)
	}
}

// TestOdysseus_SourceKeysSkipsOwnOutput makes sure a previous run's fragment is
// not mistaken for a conflicting source, which would make the second refresh
// fail against its own output.
func TestOdysseus_SourceKeysSkipsOwnOutput(t *testing.T) {
	dir := t.TempDir()
	own := filepath.Join(dir, "odysseus.generated.yaml")
	write(t, own, `models:
  qwen3.6-35b-a3b-ud-q6_k_xl--ody:
    cmd: llama-server -m /app/models/a.gguf --port ${PORT}
profiles:
  ody:
    pins:
      qwen3.6-35b-a3b-ud-q6_k_xl: qwen3.6-35b-a3b-ud-q6_k_xl--ody
`)
	models, profiles, err := SourceKeys([]string{own}, own)
	if err != nil {
		t.Fatalf("SourceKeys: %v", err)
	}
	if len(models) != 0 || len(profiles) != 0 {
		t.Errorf("own output was not skipped: models=%v profiles=%v", keys(models), keys(profiles))
	}
}

// TestOdysseus_GuardConflicts is the guard itself: a hand-written profile named
// "ody" must stop the write rather than let llama-swap fail the next reload with
// a duplicate identity key.
func TestOdysseus_GuardConflicts(t *testing.T) {
	res := &Result{
		Models: map[string]map[string]any{
			"qwen3.6-35b-a3b-ud-q6_k_xl--ody": {},
			"brand-new--ody":                  {},
		},
		Profiles: map[string]map[string]any{"ody": {}, "fresh": {}},
	}

	conflicts := GuardConflicts(res,
		map[string]string{"qwen3.6-35b-a3b-ud-q6_k_xl--ody": "config.yaml"},
		map[string]string{"ody": "config.yaml"})

	if len(conflicts) != 2 {
		t.Fatalf("got %d conflicts, want 2: %v", len(conflicts), conflicts)
	}
	joined := strings.Join(conflicts, "\n")
	if !strings.Contains(joined, "qwen3.6-35b-a3b-ud-q6_k_xl--ody") || !strings.Contains(joined, "config.yaml") {
		t.Errorf("model conflict not described usefully:\n%s", joined)
	}
	if !strings.Contains(joined, `profile "ody"`) {
		t.Errorf("profile conflict not described usefully:\n%s", joined)
	}
	if strings.Contains(joined, "fresh") || strings.Contains(joined, "brand-new") {
		t.Errorf("a non-conflicting key was reported:\n%s", joined)
	}
}

// TestOdysseus_GuardConflictsClean returns nothing when the fragment's keys are
// all new, which is the expected steady state.
func TestOdysseus_GuardConflictsClean(t *testing.T) {
	res := &Result{
		Models:   map[string]map[string]any{"a--ody": {}},
		Profiles: map[string]map[string]any{"ody": {}},
	}
	if got := GuardConflicts(res, map[string]string{"b": "config.yaml"}, map[string]string{"coding": "config.yaml"}); len(got) != 0 {
		t.Errorf("unexpected conflicts: %v", got)
	}
}

// TestOdysseus_SourceKeysToleratesMissingAndMalformed keeps a refresh working
// when an unrelated config file is absent or broken -- llama-swap reports those
// itself, with better messages.
func TestOdysseus_SourceKeysToleratesMissingAndMalformed(t *testing.T) {
	dir := t.TempDir()
	broken := filepath.Join(dir, "broken.yaml")
	write(t, broken, "models: [this is: not, a mapping\n")

	models, profiles, err := SourceKeys([]string{
		filepath.Join(dir, "does-not-exist.yaml"),
		broken,
	}, "")
	if err != nil {
		t.Fatalf("SourceKeys should tolerate these: %v", err)
	}
	if len(models) != 0 || len(profiles) != 0 {
		t.Errorf("unexpected keys: %v %v", keys(models), keys(profiles))
	}
}

func write(t *testing.T, path, content string) {
	t.Helper()
	if err := os.WriteFile(path, []byte(content), 0o644); err != nil {
		t.Fatalf("writing %s: %v", path, err)
	}
}
