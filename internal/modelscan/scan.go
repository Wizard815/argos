// Package modelscan implements directory scanning that turns .gguf files
// found on disk into a generated llama-swap `models:` YAML fragment, so a
// large, frequently-changing model directory does not need to be hand
// maintained in config.yaml.
//
// The generated fragment is written to its own file inside the process's
// --config-dir. It is never merged into the user's hand-written config.yaml
// directly; llama-swap's own -config-dir merge (internal/config/merge.go)
// combines it with the rest of the configuration, and (when -watch-config is
// enabled) the existing poll-based config watcher picks up the change and
// hot-reloads automatically. This package does not trigger a reload itself.
package modelscan

import (
	"bytes"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"

	"gopkg.in/yaml.v3"
)

// Options configures a scan.
type Options struct {
	// Dirs are the directories to scan, recursively, for model files.
	Dirs []string

	// Extensions are the file extensions to treat as models, e.g. []string{".gguf"}.
	// Defaults to []string{".gguf"} when empty.
	Extensions []string

	// CmdTemplate is the ModelConfig.Cmd value to use for every discovered
	// model, with the literal substring "${MODEL_PATH}" replaced by the
	// model's absolute file path. Any llama-swap macro such as "${PORT}" or
	// a user-defined macro name is left untouched, so it is still resolved
	// by llama-swap itself at process-launch time.
	CmdTemplate string

	// NamePrefix is prepended to every generated model ID, e.g. "local-".
	NamePrefix string

	// OutputPath is the file to write. It must live inside the directory
	// passed as -config-dir so llama-swap's config-dir merge (and, with
	// -watch-config, its file watcher) picks it up.
	OutputPath string
}

// generatedModel mirrors the subset of config.ModelConfig fields this
// package writes, kept local to avoid an import of internal/config (which
// would otherwise be a natural dependency, but pulls in the full config
// validation/macro package for no benefit here — this package only ever
// writes cmd, so a small local struct with matching yaml tags is enough
// and keeps modelscan buildable/testable independent of config).
type generatedModel struct {
	Cmd string `yaml:"cmd"`
}

type generatedFile struct {
	Models map[string]generatedModel `yaml:"models"`
}

var nonAlnum = regexp.MustCompile(`[^a-zA-Z0-9._-]+`)

// sanitizeName turns a filename (without extension) into a safe model ID:
// lowercase, alphanumeric/dot/dash/underscore only, no leading/trailing dash.
func sanitizeName(base string) string {
	name := strings.ToLower(base)
	name = nonAlnum.ReplaceAllString(name, "-")
	name = strings.Trim(name, "-")
	if name == "" {
		name = "model"
	}
	return name
}

// Scan walks opts.Dirs recursively for files matching opts.Extensions,
// builds one models: entry per file (name collisions get a short suffix
// derived from the parent directory to disambiguate), and returns the
// rendered YAML bytes along with the sorted list of model IDs found.
// It does not write anything to disk; call Write separately.
func Scan(opts Options) ([]byte, []string, error) {
	exts := opts.Extensions
	if len(exts) == 0 {
		exts = []string{".gguf"}
	}
	extSet := make(map[string]struct{}, len(exts))
	for _, e := range exts {
		extSet[strings.ToLower(e)] = struct{}{}
	}

	if strings.TrimSpace(opts.CmdTemplate) == "" {
		return nil, nil, fmt.Errorf("modelscan: CmdTemplate must not be empty")
	}

	type found struct {
		id   string
		path string
	}
	var all []found
	seen := make(map[string]int) // id -> count, to disambiguate collisions

	for _, dir := range opts.Dirs {
		absDir, err := filepath.Abs(dir)
		if err != nil {
			return nil, nil, fmt.Errorf("modelscan: resolving dir %q: %w", dir, err)
		}
		if _, err := os.Stat(absDir); err != nil {
			// A configured dir that doesn't exist (yet) is not fatal — skip it
			// so one bad/unmounted path doesn't take down the whole scan.
			continue
		}

		err = filepath.Walk(absDir, func(path string, info os.FileInfo, err error) error {
			if err != nil {
				return err
			}
			if info.IsDir() {
				return nil
			}
			ext := strings.ToLower(filepath.Ext(path))
			if _, ok := extSet[ext]; !ok {
				return nil
			}
			base := strings.TrimSuffix(filepath.Base(path), filepath.Ext(path))

			// mmproj files are vision-projector weights meant to be passed
			// via llama-server's --mmproj flag alongside a main model's -m,
			// not run standalone — skip them so they don't show up as
			// selectable models of their own.
			if strings.Contains(strings.ToLower(base), "mmproj") {
				return nil
			}

			id := opts.NamePrefix + sanitizeName(base)

			if n, dup := seen[id]; dup {
				seen[id] = n + 1
				parent := sanitizeName(filepath.Base(filepath.Dir(path)))
				id = fmt.Sprintf("%s-%s-%d", id, parent, n+1)
			} else {
				seen[id] = 0
			}

			all = append(all, found{id: id, path: path})
			return nil
		})
		if err != nil {
			return nil, nil, fmt.Errorf("modelscan: walking %q: %w", absDir, err)
		}
	}

	sort.Slice(all, func(i, j int) bool { return all[i].id < all[j].id })

	models := make(map[string]generatedModel, len(all))
	names := make([]string, 0, len(all))
	for _, f := range all {
		cmd := strings.ReplaceAll(opts.CmdTemplate, "${MODEL_PATH}", f.path)
		models[f.id] = generatedModel{Cmd: cmd}
		names = append(names, f.id)
	}

	out := generatedFile{Models: models}
	var buf bytes.Buffer
	buf.WriteString("# Generated by modelscan — do not hand-edit, it will be overwritten\n" +
		"# on the next scan. Re-run the scan (POST /api/models/rescan) to refresh.\n")
	enc := yaml.NewEncoder(&buf)
	enc.SetIndent(2)
	if err := enc.Encode(out); err != nil {
		return nil, nil, fmt.Errorf("modelscan: encoding yaml: %w", err)
	}
	enc.Close()

	return buf.Bytes(), names, nil
}

// WriteIfChanged writes data to opts.OutputPath, but only if the content
// differs from what's already there (or the file doesn't exist yet).
// Returns whether it wrote. Skipping a no-op write matters here: llama-swap's
// -watch-config watcher reloads on any file change, and a reload tears down
// and restarts every currently-running model process — so scanning on every
// /v1/models call must not cause a write (and therefore a restart of
// already-loaded models) when nothing on disk actually changed.
func WriteIfChanged(path string, data []byte) (bool, error) {
	existing, err := os.ReadFile(path)
	if err == nil && bytes.Equal(existing, data) {
		return false, nil
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return false, fmt.Errorf("modelscan: creating output dir: %w", err)
	}
	if err := os.WriteFile(path, data, 0o644); err != nil {
		return false, fmt.Errorf("modelscan: writing %q: %w", path, err)
	}
	return true, nil
}
