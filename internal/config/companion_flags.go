package config

import (
	"os"
	"path/filepath"
	"strings"
)

// Companion flags: what some llama-server flag combinations need to run at all.
// Added to a model's argv at launch (SanitizedCommand), whatever wrote the
// command: config.yaml, the builder, or a profile pulled from Odysseus.
// The builder applies the same rule when it shows and exports a command
// (builder/src/always.py combo_fixes).
//
// --split-mode tensor with an MTP draft: by default the draft model is split
// across the cards like the main model, and llama.cpp's flash attention has no
// kernel for some of the shapes that gives (Gemma 4's 512-wide heads with one KV
// head per card, a quantised cache, the draft's 2-4 token batches), so the
// server aborts mid-request (fattn.cu "fatal error" under draft_mtp). The draft
// goes whole on one card (--device-draft) and, when the main cache is
// quantised, keeps an f16 cache of its own. Flags the command sets win.

// devicePresent reports whether a device node exists; a variable so tests can
// pretend to be an AMD or an NVIDIA host.
var devicePresent = func(path string) bool {
	_, err := os.Stat(path)
	return err == nil
}

var unquantisedCache = map[string]bool{"f16": true, "bf16": true, "f32": true}

// flagValue finds a flag in either spelling, as "--flag value" or
// "--flag=value". found is true for a flag that is present without a value.
func flagValue(args []string, names ...string) (value string, found bool) {
	for i, a := range args {
		for _, n := range names {
			if a == n {
				if i+1 < len(args) {
					return args[i+1], true
				}
				return "", true
			}
			if strings.HasPrefix(a, n+"=") {
				return a[len(n)+1:], true
			}
		}
	}
	return "", false
}

func hasMTPDraft(args []string) bool {
	if st, _ := flagValue(args, "--spec-type"); strings.Contains(strings.ToLower(st), "mtp") {
		return true
	}
	md, _ := flagValue(args, "-md", "--model-draft")
	return md != "" && strings.Contains(strings.ToLower(filepath.Base(md)), "mtp")
}

// draftDevice is where the draft goes: the first of the command's own --device
// list, else card 0 of the runtime it runs on ("" when that cannot be told).
func draftDevice(args []string) string {
	if list, ok := flagValue(args, "-dev", "--device"); ok {
		for _, d := range strings.Split(list, ",") {
			if d = strings.TrimSpace(d); d != "" && !strings.EqualFold(d, "none") {
				return d
			}
		}
	}
	if len(args) > 1 {
		prog := filepath.Base(args[0])
		if (prog == "remote-llama" || prog == "docker-llama") && !strings.HasPrefix(args[1], "-") {
			switch strings.ToLower(strings.SplitN(args[1], "_", 2)[0]) { // "rocm", or "rocm_srv3" for one server
			case "rocm":
				return "ROCm0"
			case "cuda":
				return "CUDA0"
			}
		}
	}
	switch {
	case devicePresent("/dev/kfd"):
		return "ROCm0"
	case devicePresent("/dev/nvidiactl"):
		return "CUDA0"
	}
	return ""
}

// CompanionFlags returns the arguments args lacks for the combinations above
// (nil for a command that needs none).
func CompanionFlags(args []string) []string {
	sm, _ := flagValue(args, "-sm", "--split-mode")
	if !strings.EqualFold(strings.TrimSpace(sm), "tensor") || !hasMTPDraft(args) {
		return nil
	}
	var out []string
	if _, set := flagValue(args, "-devd", "--device-draft"); !set {
		if dev := draftDevice(args); dev != "" {
			out = append(out, "--device-draft", dev)
		}
	}
	k, _ := flagValue(args, "-ctk", "--cache-type-k")
	v, vset := flagValue(args, "-ctv", "--cache-type-v")
	k = strings.ToLower(strings.TrimSpace(k))
	if k == "" {
		k = "f16"
	}
	v = strings.ToLower(strings.TrimSpace(v))
	if !vset || v == "" {
		v = k
	}
	_, kd := flagValue(args, "-ctkd", "--cache-type-k-draft")
	_, vd := flagValue(args, "-ctvd", "--cache-type-v-draft")
	if (!unquantisedCache[k] || !unquantisedCache[v]) && !kd && !vd {
		out = append(out, "--cache-type-k-draft", "f16", "--cache-type-v-draft", "f16")
	}
	return out
}

// CompanionFlags of this model's command (for the launch log).
func (m *ModelConfig) CompanionFlags() []string {
	args, err := SanitizeCommand(m.Cmd)
	if err != nil {
		return nil
	}
	return CompanionFlags(args)
}
