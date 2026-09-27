package odysseus

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"

	"gopkg.in/yaml.v3"

	"github.com/mostlygeek/llama-swap/internal/modelscan"
)

// RefreshResult reports what a refresh did.
type RefreshResult struct {
	// Models and Profiles are how many of each the fragment now defines.
	Models   int
	Profiles int
	// Wrote is false when the fragment was already up to date. That matters:
	// llama-swap reloads on any change under -config-dir, and a reload restarts
	// every running model, so a no-op refresh must not touch the file.
	Wrote bool
	// Warnings are per-task advisories (a skipped vLLM task, a command with no
	// port, and so on) plus anything the guard wanted to say that was not fatal.
	Warnings []string

	// ProfileName is the union profile the refresh generated, empty when no
	// per-model choices are set.
	ProfileName string
	// ChoicesResolved maps model ID -> variant ID for each choice that resolved.
	ChoicesResolved map[string]string
}

// Refresh fetches Odysseus' cookbook state, renders the llama-swap fragment and
// writes it if its content changed.
//
// otherSources are the paths of every other YAML config source in play (the
// -config file and the other files under -config-dir). They are read to make
// sure this fragment does not redefine a model or profile that another source
// already owns: llama-swap's merge treats `models` and `profiles` as
// identity-keyed maps and rejects a key defined twice
// (internal/config/merge.go identityMapPaths), which would otherwise fail the
// whole config load the next time anything reloads.
func Refresh(ctx context.Context, opts Options, outPath string, otherSources []string) (RefreshResult, error) {
	var rr RefreshResult

	// per-model choices live beside the fragment; load them so a refresh
	// regenerates the union profile with whatever the operator last picked.
	if opts.ChoicesPath != "" {
		cs, err := LoadChoices(opts.ChoicesPath)
		if err != nil {
			return rr, err
		}
		opts.Choices = cs.Choices
	}

	state, err := FetchState(ctx, opts)
	if err != nil {
		return rr, err
	}
	res, err := Build(state, opts)
	if err != nil {
		return rr, err
	}
	rr.ProfileName = res.ProfileName
	rr.ChoicesResolved = res.ChoicesResolved
	rr.Warnings = res.Warnings

	otherModels, otherProfiles, err := SourceKeys(otherSources, outPath)
	if err != nil {
		return rr, err
	}
	if conflicts := GuardConflicts(res, otherModels, otherProfiles); len(conflicts) > 0 {
		return rr, fmt.Errorf("refusing to write %s: %s -- rename odysseus.profilePrefix, "+
			"or remove the conflicting entries, then refresh again",
			outPath, strings.Join(conflicts, "; "))
	}

	data, err := Render(res)
	if err != nil {
		return rr, err
	}
	rr.Models = len(res.Models)
	rr.Profiles = len(res.Profiles)
	if rr.Wrote, err = modelscan.WriteIfChanged(outPath, data); err != nil {
		return rr, err
	}
	return rr, nil
}

// SourceKeys reads the identity keys a set of YAML config sources define,
// returning key -> source path for the `models` and `profiles` maps.
//
// skipPath is excluded from the result; pass the fragment being generated so a
// previous run's output does not look like a conflicting source.
func SourceKeys(paths []string, skipPath string) (models, profiles map[string]string, err error) {
	models = map[string]string{}
	profiles = map[string]string{}

	skipAbs := ""
	if skipPath != "" {
		if abs, aerr := filepath.Abs(skipPath); aerr == nil {
			skipAbs = abs
		}
	}

	for _, p := range paths {
		if strings.TrimSpace(p) == "" {
			continue
		}
		abs, aerr := filepath.Abs(p)
		if aerr != nil {
			return nil, nil, fmt.Errorf("resolving %s: %w", p, aerr)
		}
		if skipAbs != "" && abs == skipAbs {
			continue
		}
		raw, rerr := os.ReadFile(p)
		if rerr != nil {
			if os.IsNotExist(rerr) {
				continue
			}
			return nil, nil, fmt.Errorf("reading config source %s: %w", p, rerr)
		}
		var doc yaml.Node
		if uerr := yaml.Unmarshal(raw, &doc); uerr != nil {
			// Not this package's problem to report; llama-swap will fail the
			// load with a better message if the file really is malformed.
			continue
		}
		collectKeys(&doc, "models", p, models)
		collectKeys(&doc, "profiles", p, profiles)
	}
	return models, profiles, nil
}

// collectKeys records the child keys of a top-level identity map.
func collectKeys(doc *yaml.Node, section, source string, into map[string]string) {
	root := doc
	if root.Kind == yaml.DocumentNode && len(root.Content) > 0 {
		root = root.Content[0]
	}
	if root.Kind != yaml.MappingNode {
		return
	}
	for i := 0; i+1 < len(root.Content); i += 2 {
		if root.Content[i].Value != section {
			continue
		}
		val := root.Content[i+1]
		if val.Kind != yaml.MappingNode {
			return
		}
		for j := 0; j+1 < len(val.Content); j += 2 {
			key := val.Content[j].Value
			if _, seen := into[key]; !seen {
				into[key] = source
			}
		}
		return
	}
}

// GuardConflicts lists entries in res that another config source already
// defines. Empty means the fragment is safe to write.
//
// Sorted for a stable error message.
func GuardConflicts(res *Result, otherModels, otherProfiles map[string]string) []string {
	var out []string
	for id := range res.Models {
		if src, clash := otherModels[id]; clash {
			out = append(out, fmt.Sprintf("model %q is already defined in %s", id, src))
		}
	}
	for name := range res.Profiles {
		if src, clash := otherProfiles[name]; clash {
			out = append(out, fmt.Sprintf("profile %q is already defined in %s", name, src))
		}
	}
	sort.Strings(out)
	return out
}
