package server

import (
	"context"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"time"
)

// The single_gpu half of the GPU toggle mode is applied here, at launch time:
// the model's HIP_VISIBLE_DEVICES is rewritten to a card that actually has
// room, instead of trusting whatever the launch command pins.

const (
	// gpuLaunchMarginGiB is the slack left on a card for the KV cache and
	// compute buffers when deciding whether a model fits.
	gpuLaunchMarginGiB = 4
)

// drmRoot is the kernel sysfs root. Reading it works on MI50 / gfx906 (and
// every modern AMD card) without depending on rocm-smi's output format. A var,
// not a const, so tests can point it at a temporary tree.
var drmRoot = "/sys/class/drm"

var cardPattern = regexp.MustCompile(`^card(\d+)$`)

// readFreeVRAMGiB returns {gpu id: free GiB}.
//
// rocm-smi is preferred, because it reports in ROCm's GPU numbering — the same
// numbering HIP_VISIBLE_DEVICES uses — so a caller can feed an id straight back
// as a device. The sysfs fallback uses /sys/class/drm/cardN ids, which are NOT
// guaranteed to line up with the ROCm index, so it is only used when rocm-smi
// is unavailable.
func readFreeVRAMGiB(root string) map[string]int {
	if out := freeVRAMFromROCmSMI(); len(out) > 0 {
		return out
	}
	return freeVRAMFromSysfs(root)
}

// freeVRAMFromSysfs reads the kernel sysfs directly. Its ids are DRM card
// numbers. Cards whose driver exposes neither mem_info_vram_free nor the
// total/used pair (an Intel iGPU, say) are skipped.
func freeVRAMFromSysfs(root string) map[string]int {
	out := map[string]int{}
	entries, err := os.ReadDir(root)
	if err != nil {
		return out
	}
	for _, e := range entries {
		m := cardPattern.FindStringSubmatch(e.Name())
		if m == nil {
			continue
		}
		dev := filepath.Join(root, e.Name(), "device")
		free := readCounter(filepath.Join(dev, "mem_info_vram_free"))
		if free < 0 {
			// Older kernels expose only the total/used pair.
			total := readCounter(filepath.Join(dev, "mem_info_vram_total"))
			used := readCounter(filepath.Join(dev, "mem_info_vram_used"))
			if total >= 0 && used >= 0 {
				free = total - used
			}
		}
		if free < 0 {
			continue
		}
		out[m[1]] = int(free / (1024 * 1024 * 1024))
	}
	return out
}

// readCounter reads a non-negative integer from a sysfs file, or -1 when it is
// missing or malformed.
func readCounter(path string) int64 {
	data, err := os.ReadFile(path)
	if err != nil {
		return -1
	}
	v, err := strconv.ParseInt(strings.TrimSpace(string(data)), 10, 64)
	if err != nil || v < 0 {
		return -1
	}
	return v
}

// rocmSMIVRAMArgs is the rocm-smi invocation used to read VRAM. A var so a test
// can point it at a fixture.
var rocmSMIVRAMArgs = []string{"rocm-smi", "--showmeminfo", "vram"}

// freeVRAMFromROCmSMI runs rocm-smi and parses its per-GPU total/used VRAM.
// Returns nil when rocm-smi is missing, errors, or reports nothing usable, so
// the caller can fall back to sysfs.
func freeVRAMFromROCmSMI() map[string]int {
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	out, err := exec.CommandContext(ctx, rocmSMIVRAMArgs[0], rocmSMIVRAMArgs[1:]...).Output()
	if err != nil {
		return nil
	}
	return parseROCmSMIVRAM(string(out))
}

// rocmGPULine matches a rocm-smi line like
//
//	GPU[1]      : VRAM Total Used Memory (B): 32212254720
var rocmGPULine = regexp.MustCompile(`^GPU\[(\d+)\]\s*:\s*(.+?):\s*(\d+)\s*$`)

// parseROCmSMIVRAM reads `rocm-smi --showmeminfo vram` output into free GiB per
// GPU. The ids are ROCm's GPU indices — the numbering HIP_VISIBLE_DEVICES takes
// — which is the whole point of preferring rocm-smi over /sys/class/drm.
func parseROCmSMIVRAM(text string) map[string]int {
	type mem struct{ total, used int64 }
	perGPU := map[string]*mem{}
	var order []string

	for _, line := range strings.Split(text, "\n") {
		m := rocmGPULine.FindStringSubmatch(strings.TrimSpace(line))
		if m == nil {
			continue
		}
		id, label := m[1], strings.ToLower(m[2])
		value, err := strconv.ParseInt(m[3], 10, 64)
		if err != nil {
			continue
		}
		if perGPU[id] == nil {
			perGPU[id] = &mem{}
			order = append(order, id)
		}
		// "used" is checked first: the used label also contains "total".
		switch {
		case strings.Contains(label, "used"):
			perGPU[id].used = value
		case strings.Contains(label, "total"):
			perGPU[id].total = value
		}
	}

	out := map[string]int{}
	for _, id := range order {
		g := perGPU[id]
		if g.total <= 0 {
			continue
		}
		free := g.total - g.used
		if free < 0 {
			free = 0
		}
		out[id] = int(free / (1024 * 1024 * 1024))
	}
	return out
}

// modelPathFromArgv finds the -m / --model value in an expanded command.
func modelPathFromArgv(argv []string) string {
	for i := 0; i < len(argv); i++ {
		t := argv[i]
		if (t == "-m" || t == "--model") && i+1 < len(argv) {
			return argv[i+1]
		}
		if strings.HasPrefix(t, "--model=") {
			return strings.TrimPrefix(t, "--model=")
		}
	}
	return ""
}

// modelSizeGiB estimates the weight size from the model file, rounded up. Zero
// means "unknown", which the caller treats as "skip the fit test".
func modelSizeGiB(argv []string) int {
	path := modelPathFromArgv(argv)
	if path == "" {
		return 0
	}
	fi, err := os.Stat(path)
	if err != nil {
		return 0
	}
	return int((fi.Size() + (1 << 30) - 1) / (1 << 30))
}

// envValue returns the value of key in a KEY=value env list.
func envValue(env []string, key string) string {
	prefix := key + "="
	for _, e := range env {
		if strings.HasPrefix(e, prefix) {
			return strings.TrimPrefix(e, prefix)
		}
	}
	return ""
}

// setEnvValue replaces key in env, or appends it when absent.
func setEnvValue(env []string, key, value string) []string {
	prefix := key + "="
	out := make([]string, 0, len(env)+1)
	replaced := false
	for _, e := range env {
		if strings.HasPrefix(e, prefix) {
			if !replaced {
				out = append(out, key+"="+value)
				replaced = true
			}
			continue
		}
		out = append(out, e)
	}
	if !replaced {
		out = append(out, key+"="+value)
	}
	return out
}

// chooseSingleGPU picks the card a single_gpu model should bind to.
//
// Preference: the card the launch already pins, if it still has room; else the
// emptiest card that fits; else the emptiest card at all (the launch will fail
// on its own if it truly does not fit, which beats silently binding somewhere
// with no chance). The bool reports whether the choice fits; "" with false
// means there was nothing to choose from and the caller should leave the env
// alone.
func chooseSingleGPU(env, argv []string, root string) (string, bool) {
	free := readFreeVRAMGiB(root)
	if len(free) == 0 {
		return "", false
	}
	needed := modelSizeGiB(argv) + gpuLaunchMarginGiB
	if needed <= gpuLaunchMarginGiB {
		needed = 0 // unknown model size: skip the fit test rather than reject everything
	}
	fits := func(g string) bool { return needed == 0 || free[g] >= needed }

	// A hard pin is honoured only while that card still fits.
	pin := envValue(env, "HIP_VISIBLE_DEVICES")
	if pin != "" && !strings.Contains(pin, ",") && fits(pin) {
		return pin, true
	}

	best, bestFree := "", -1
	for g, f := range free {
		if fits(g) && f > bestFree {
			best, bestFree = g, f
		}
	}
	if best != "" {
		return best, true
	}

	// Nothing fits: hand back the emptiest card; the caller warns.
	best, bestFree = "", -1
	for g, f := range free {
		if f > bestFree {
			best, bestFree = g, f
		}
	}
	return best, false
}

// launchEnv is the process.LaunchEnvPolicy installed by the server: it applies
// a model's GPU mode to its environment just before the upstream starts.
func (s *Server) launchEnv(modelID string, argv, env []string) []string {
	if s.ModelMode(modelID) != ModeSingleGPU {
		return env
	}
	device, fits := chooseSingleGPU(env, argv, drmRoot)
	if device == "" {
		return env
	}
	if fits {
		s.proxylog.Infof("gpu mode: binding %s to GPU %s (single_gpu)", modelID, device)
	} else {
		s.proxylog.Warnf("gpu mode: %s fits no card comfortably (weights + %d GiB margin); binding GPU %s anyway",
			modelID, gpuLaunchMarginGiB, device)
	}
	return setEnvValue(env, "HIP_VISIBLE_DEVICES", device)
}
