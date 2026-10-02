package server

import (
	"os"
	"path/filepath"
	"strconv"
	"testing"
)

// writeCard creates a fake /sys/class/drm card directory exposing free VRAM.
func writeCard(t *testing.T, root, id string, freeGiB int) {
	t.Helper()
	dev := filepath.Join(root, "card"+id, "device")
	if err := os.MkdirAll(dev, 0o755); err != nil {
		t.Fatalf("mkdir: %v", err)
	}
	free := strconv.FormatInt(int64(freeGiB)<<30, 10)
	if err := os.WriteFile(filepath.Join(dev, "mem_info_vram_free"), []byte(free+"\n"), 0o644); err != nil {
		t.Fatalf("write: %v", err)
	}
}

// sparseModel makes a sparse file of sizeGiB so the fit test sees a realistic
// weight size without using disk.
func sparseModel(t *testing.T, sizeGiB int) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "model.gguf")
	f, err := os.Create(path)
	if err != nil {
		t.Fatalf("create: %v", err)
	}
	if err := f.Truncate(int64(sizeGiB) << 30); err != nil {
		t.Fatalf("truncate: %v", err)
	}
	f.Close()
	return path
}

func TestServer_ChooseSingleGPUHonoursPinWhenItFits(t *testing.T) {
	root := t.TempDir()
	writeCard(t, root, "0", 30)
	writeCard(t, root, "1", 31)
	model := sparseModel(t, 20) // needs 24

	dev, fits := chooseSingleGPU([]string{"HIP_VISIBLE_DEVICES=0"},
		[]string{"llama-server", "-m", model}, root)
	if dev != "0" || !fits {
		t.Errorf("pin 0 fits, want device 0 fits=true, got %q fits=%v", dev, fits)
	}
}

func TestServer_ChooseSingleGPUSkipsFullPinnedCard(t *testing.T) {
	root := t.TempDir()
	writeCard(t, root, "0", 5)
	writeCard(t, root, "1", 30)
	model := sparseModel(t, 20) // needs 24

	dev, fits := chooseSingleGPU([]string{"HIP_VISIBLE_DEVICES=0"},
		[]string{"llama-server", "-m", model}, root)
	if dev != "1" || !fits {
		t.Errorf("pin 0 is full, want device 1, got %q fits=%v", dev, fits)
	}
}

func TestServer_ChooseSingleGPUPicksEmptiestFit(t *testing.T) {
	root := t.TempDir()
	writeCard(t, root, "0", 25)
	writeCard(t, root, "1", 30)
	model := sparseModel(t, 20)

	dev, fits := chooseSingleGPU(nil, []string{"llama-server", "-m", model}, root)
	if dev != "1" || !fits {
		t.Errorf("want the emptiest fitting card 1, got %q fits=%v", dev, fits)
	}
}

func TestServer_ChooseSingleGPUNoFitFallsBackToEmptiest(t *testing.T) {
	root := t.TempDir()
	writeCard(t, root, "0", 10)
	writeCard(t, root, "1", 12)
	model := sparseModel(t, 20)

	dev, fits := chooseSingleGPU(nil, []string{"llama-server", "-m", model}, root)
	if dev != "1" || fits {
		t.Errorf("nothing fits, want emptiest 1 with fits=false, got %q fits=%v", dev, fits)
	}
}

func TestServer_ChooseSingleGPUIgnoresSplitPin(t *testing.T) {
	root := t.TempDir()
	writeCard(t, root, "0", 30)
	writeCard(t, root, "1", 29)
	model := sparseModel(t, 20)

	dev, fits := chooseSingleGPU([]string{"HIP_VISIBLE_DEVICES=0,1"},
		[]string{"llama-server", "-m", model}, root)
	if dev != "0" || !fits {
		t.Errorf("a split pin is not a single choice, want emptiest 0, got %q fits=%v", dev, fits)
	}
}

func TestServer_ChooseSingleGPUNoCards(t *testing.T) {
	dev, fits := chooseSingleGPU(nil, []string{"llama-server"}, t.TempDir())
	if dev != "" || fits {
		t.Errorf("no cards should yield no choice, got %q fits=%v", dev, fits)
	}
}

func TestServer_LaunchEnvAppliesSingleGPUMode(t *testing.T) {
	root := t.TempDir()
	writeCard(t, root, "0", 5)
	writeCard(t, root, "1", 30)
	old := drmRoot
	drmRoot = root
	t.Cleanup(func() { drmRoot = old })

	cfg := modelTogglesTestConfig(t, filepath.Join(t.TempDir(), "toggles.json"))
	s := profileTestServer(t, cfg, newStubRouter([]string{"real"}, "ok"))
	s.modelModesMu.Lock()
	s.modelModes = map[string]string{"real": ModeSingleGPU}
	s.modelModesMu.Unlock()

	model := sparseModel(t, 20)
	env := s.launchEnv("real", []string{"llama-server", "-m", model},
		[]string{"HIP_VISIBLE_DEVICES=0"})
	if got := envValue(env, "HIP_VISIBLE_DEVICES"); got != "1" {
		t.Errorf("single_gpu should move real off the full card, got %q (%v)", got, env)
	}
}

func TestServer_LaunchEnvLeavesStandardAlone(t *testing.T) {
	cfg := modelTogglesTestConfig(t, filepath.Join(t.TempDir(), "toggles.json"))
	s := profileTestServer(t, cfg, newStubRouter([]string{"real"}, "ok"))

	env := []string{"HIP_VISIBLE_DEVICES=0,1"}
	got := s.launchEnv("real", []string{"llama-server"}, env)
	if len(got) != 1 || got[0] != "HIP_VISIBLE_DEVICES=0,1" {
		t.Errorf("standard must leave the env alone, got %v", got)
	}
}
