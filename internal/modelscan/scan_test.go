package modelscan

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"gopkg.in/yaml.v3"
)

// writeFakeModel creates a zero-byte .gguf so Scan has something to find.
func writeFakeModel(t *testing.T, dir, name string) string {
	t.Helper()
	p := filepath.Join(dir, name)
	if err := os.WriteFile(p, []byte("not a real gguf"), 0o644); err != nil {
		t.Fatalf("writing %s: %v", p, err)
	}
	return p
}

// genFile mirrors the shape llama-swap itself parses out of the generated
// fragment, so a test failure here means llama-swap would misread the file.
type genFile struct {
	Models map[string]struct {
		Cmd string   `yaml:"cmd"`
		Env []string `yaml:"env"`
		TTL *int     `yaml:"ttl"`
	} `yaml:"models"`
}

func scanAndParse(t *testing.T, opts Options) genFile {
	t.Helper()
	data, _, err := Scan(opts)
	if err != nil {
		t.Fatalf("Scan: %v", err)
	}
	var out genFile
	if err := yaml.Unmarshal(data, &out); err != nil {
		t.Fatalf("unmarshal generated yaml: %v", err)
	}
	return out
}

// TestScan_OmitsEnvAndTTLWhenUnset guards the existing behaviour: a scan that
// does not ask for env/ttl must not start writing them, because llama-swap
// reloads (and restarts every running model) whenever the generated file
// changes.
func TestScan_OmitsEnvAndTTLWhenUnset(t *testing.T) {
	dir := t.TempDir()
	writeFakeModel(t, dir, "some-model.gguf")

	out := scanAndParse(t, Options{
		Dirs:        []string{dir},
		CmdTemplate: "llama-server -m ${MODEL_PATH}",
	})

	m, ok := out.Models["some-model"]
	if !ok {
		t.Fatalf("model not found; got %v", out.Models)
	}
	if m.Env != nil {
		t.Errorf("Env = %v, want nil", m.Env)
	}
	if m.TTL != nil {
		t.Errorf("TTL = %v, want nil", *m.TTL)
	}
	if strings.Contains(string(mustScan(t, Options{Dirs: []string{dir}, CmdTemplate: "x ${MODEL_PATH}"})), "env:") {
		t.Errorf("generated yaml mentions env: when no env was requested")
	}
}

// TestScan_WritesEnvAndTTL checks the new fields reach every generated model.
func TestScan_WritesEnvAndTTL(t *testing.T) {
	dir := t.TempDir()
	writeFakeModel(t, dir, "one.gguf")
	writeFakeModel(t, dir, "two.gguf")

	zero := 0
	out := scanAndParse(t, Options{
		Dirs:        []string{dir},
		CmdTemplate: "llama-server -m ${MODEL_PATH}",
		Env:         []string{"HIP_VISIBLE_DEVICES=0", "GPU_MAX_HW_QUEUES=8"},
		TTL:         &zero,
	})

	if len(out.Models) != 2 {
		t.Fatalf("got %d models, want 2", len(out.Models))
	}
	for id, m := range out.Models {
		if len(m.Env) != 2 || m.Env[0] != "HIP_VISIBLE_DEVICES=0" {
			t.Errorf("%s: Env = %v, want the two requested vars", id, m.Env)
		}
		if m.TTL == nil {
			t.Errorf("%s: TTL is nil, want 0", id)
			continue
		}
		// 0 must survive as 0 -- it means "never unload" to llama-swap, and
		// must not be confused with unset (-1 means inherit globalTTL).
		if *m.TTL != 0 {
			t.Errorf("%s: TTL = %d, want 0", id, *m.TTL)
		}
		if !strings.Contains(m.Cmd, filepath.Join(dir, id)) {
			t.Errorf("%s: cmd %q does not contain the model path", id, m.Cmd)
		}
	}
}

// TestScan_TTLZeroIsNotOmitted is the specific trap: *int with omitempty must
// still emit ttl: 0, because a pointer to zero is not the same as nil.
func TestScan_TTLZeroIsNotOmitted(t *testing.T) {
	dir := t.TempDir()
	writeFakeModel(t, dir, "m.gguf")

	zero := 0
	out := scanAndParse(t, Options{
		Dirs:        []string{dir},
		CmdTemplate: "llama-server -m ${MODEL_PATH}",
		TTL:         &zero,
	})
	if out.Models["m"].TTL == nil {
		t.Fatal("ttl: 0 was dropped from the generated yaml")
	}
	if !strings.Contains(string(mustScan(t, Options{
		Dirs:        []string{dir},
		CmdTemplate: "llama-server -m ${MODEL_PATH}",
		TTL:         &zero,
	})), "ttl: 0") {
		t.Error("generated yaml does not contain the literal 'ttl: 0'")
	}
}

// TestScan_PositiveTTL makes sure a real timeout round-trips too.
func TestScan_PositiveTTL(t *testing.T) {
	dir := t.TempDir()
	writeFakeModel(t, dir, "m.gguf")

	n := 900
	out := scanAndParse(t, Options{
		Dirs:        []string{dir},
		CmdTemplate: "llama-server -m ${MODEL_PATH}",
		TTL:         &n,
	})
	if got := out.Models["m"].TTL; got == nil || *got != 900 {
		t.Fatalf("TTL = %v, want 900", got)
	}
}

func mustScan(t *testing.T, opts Options) []byte {
	t.Helper()
	data, _, err := Scan(opts)
	if err != nil {
		t.Fatalf("Scan: %v", err)
	}
	return data
}
