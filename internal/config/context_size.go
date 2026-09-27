package config

import (
	"path/filepath"
	"strconv"
	"strings"
)

// contextSizeFlags are the llama-server arguments that set the allocated
// context window. -c is the short form of --ctx-size; both take a separate
// value or a --flag=value form.
var contextSizeFlags = map[string]struct{}{
	"-c":         {},
	"--ctx-size": {},
}

// ContextSizeFromCommand reports the context window the model's launch command
// allocates, or 0 when the command does not pin one.
//
// A client reads the allocated window (not the GGUF training maximum) to decide
// how much conversation it may send. Once a child is running, /props answers
// that; before it is loaded, this command line is the only place llama-swap
// knows the number, and a client that cannot find it falls back to a guess for
// its truncation and compaction decisions.
//
// Only llama.cpp commands are parsed. -c means the context window there and
// something else elsewhere (an interpreter's code argument, a compiler's input
// file), and a wrong window is worse than no answer.
func ContextSizeFromCommand(cmd string) int {
	args, err := SanitizeCommand(cmd)
	if err != nil {
		return 0
	}
	if !isLlamaCppCommand(args) {
		return 0
	}

	for i, arg := range args {
		name, value, hasValue := strings.Cut(arg, "=")
		if _, ok := contextSizeFlags[name]; !ok {
			continue
		}
		if !hasValue {
			if i+1 >= len(args) {
				continue
			}
			value = args[i+1]
		}
		// -c 0 asks llama.cpp to take the window from the model, and a negative
		// value is not a window at all: report neither rather than a number the
		// client would trust.
		if n, convErr := strconv.Atoi(strings.TrimSpace(value)); convErr == nil && n > 0 {
			return n
		}
	}
	return 0
}

// isLlamaCppCommand reports whether argv runs a llama.cpp binary. Leading
// VAR=value assignments are skipped first: commands in the wild (and the
// Odysseus-generated ones) prefix the binary with environment overrides such as
// HSA_OVERRIDE_GFX_VERSION or HIP_VISIBLE_DEVICES.
func isLlamaCppCommand(args []string) bool {
	for _, arg := range args {
		if isEnvAssignment(arg) {
			continue
		}
		return strings.HasPrefix(strings.ToLower(filepath.Base(arg)), "llama")
	}
	return false
}

// isEnvAssignment reports whether arg is a leading VAR=value override rather
// than the command name itself.
func isEnvAssignment(arg string) bool {
	name, _, ok := strings.Cut(arg, "=")
	if !ok || name == "" {
		return false
	}
	for i, r := range name {
		switch {
		case r >= 'a' && r <= 'z', r >= 'A' && r <= 'Z', r == '_':
		case r >= '0' && r <= '9' && i > 0:
		default:
			return false
		}
	}
	return true
}
