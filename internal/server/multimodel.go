package server

import (
	"fmt"
	"sort"
	"strconv"
	"strings"

	"github.com/mostlygeek/llama-swap/internal/config"
)

// multiModelGroupID names the routing group the generated copies are put in.
// The group is non-exclusive and non-swapping so the copies coexist.
const multiModelGroupID = "multimodel"

// applyMultiModel expands every multi_model model into per-GPU copies fronted by
// a spillover selector, so that one model id serves ceil(chats/parallel)
// instances.
//
// It reuses llama-swap's existing spillover selector: with
// settings.spillover equal to the model's --parallel, the selector fills the
// first target up to that many concurrent requests and then spills onto the
// next copy, starting it on demand. Every additional chat therefore lands on a
// fresh instance until the copies run out.
//
// It runs on the loaded config before the routers are built, and only touches
// models whose mode is multi_model, so a config that never sets the mode is
// completely unchanged.
func applyMultiModel(cfg *config.Config, modes map[string]string, cards []string) {
	if len(cards) == 0 {
		return // no ROCm cards: nothing to spread across
	}

	// Deterministic order so the generated ids are stable across reloads.
	ids := make([]string, 0, len(modes))
	for id, mode := range modes {
		if mode == ModeMultiModel {
			ids = append(ids, id)
		}
	}
	sort.Strings(ids)

	for _, modelID := range ids {
		base, ok := cfg.Models[modelID]
		if !ok {
			continue // mode set for a model that no longer exists
		}
		parallel := parallelFromCmd(base.Cmd)
		if parallel < 1 {
			parallel = 1
		}

		targets := make([]string, 0, len(cards))
		for i, card := range cards {
			copyID := fmt.Sprintf("%s--mm%d", modelID, i)
			cp := base
			// Prefer this card, but keep the copy on single_gpu so the launch
			// policy can move it if the card is already taken.
			cp.Env = setEnvValue(base.Env, "HIP_VISIBLE_DEVICES", card)
			cp.Unlisted = true
			cfg.Models[copyID] = cp
			modes[copyID] = ModeSingleGPU
			targets = append(targets, copyID)
		}

		// The base id becomes the selector, so a request for it is resolved to a
		// copy. Drop the base from the models and from any group it was in, and
		// put the copies in the models' place.
		removeFromGroups(cfg, append([]string{modelID}, targets...)...)
		addToGroup(cfg, multiModelGroupID, targets...)
		delete(cfg.Models, modelID)

		ensureSelectors(cfg)
		cfg.Selectors[modelID] = config.SelectorConfig{
			Strategy:    config.SelectorStrategySpillover,
			Targets:     targets,
			Settings:    config.SelectorSettings{Spillover: parallel},
			Name:        base.Name,
			Description: base.Description,
			Metadata:    base.Metadata,
		}
	}
}

func ensureSelectors(cfg *config.Config) {
	if cfg.Selectors == nil {
		cfg.Selectors = map[string]config.SelectorConfig{}
	}
}

// ensureGroups returns the group map the router reads, creating it when the
// config has none, and keeps the legacy Groups field pointing at the same map
// (the loader aliases the two) so both views stay consistent and a write is not
// applied twice.
func ensureGroups(cfg *config.Config) map[string]config.GroupConfig {
	groups := cfg.Routing.Router.Settings.Groups
	if groups == nil {
		groups = cfg.Groups
	}
	if groups == nil {
		groups = map[string]config.GroupConfig{}
	}
	cfg.Routing.Router.Settings.Groups = groups
	cfg.Groups = groups
	return groups
}

// removeFromGroups drops the ids from every routing group so a renamed model is
// not left behind in a group it no longer belongs to.
func removeFromGroups(cfg *config.Config, ids ...string) {
	drop := make(map[string]bool, len(ids))
	for _, id := range ids {
		drop[id] = true
	}
	groups := ensureGroups(cfg)
	for gid, g := range groups {
		kept := make([]string, 0, len(g.Members))
		for _, m := range g.Members {
			if !drop[m] {
				kept = append(kept, m)
			}
		}
		g.Members = kept
		groups[gid] = g
	}
}

// addToGroup adds the ids to a group, creating it as non-exclusive and
// non-swapping so its members can be resident together.
func addToGroup(cfg *config.Config, gid string, ids ...string) {
	groups := ensureGroups(cfg)
	g := groups[gid]
	g.Swap = false
	g.Exclusive = false
	g.Members = append(g.Members, ids...)
	groups[gid] = g
}

// parallelFromCmd reads --parallel N (or --parallel=N / -np N) from a launch
// command, defaulting to 1. It is the number of chats one instance serves.
func parallelFromCmd(cmd string) int {
	toks := strings.Fields(cmd)
	for i, t := range toks {
		if (t == "--parallel" || t == "-np") && i+1 < len(toks) {
			if n, err := strconv.Atoi(toks[i+1]); err == nil {
				return n
			}
		}
		if strings.HasPrefix(t, "--parallel=") {
			if n, err := strconv.Atoi(strings.TrimPrefix(t, "--parallel=")); err == nil {
				return n
			}
		}
	}
	return 1
}

// listDRMCards returns the ids of the ROCm cards, sorted, so copies get a
// deterministic card each.
func listDRMCards(root string) []string {
	free := readFreeVRAMGiB(root)
	ids := make([]string, 0, len(free))
	for id := range free {
		ids = append(ids, id)
	}
	sort.Strings(ids)
	return ids
}
