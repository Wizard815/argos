package server

import (
	"fmt"
	"net"
	"net/url"
	"os"
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
func applyMultiModel(cfg *config.Config, modes map[string]string,
	pins, choices map[string]string, cards []string) {
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

		// The copies must run the model's EFFECTIVE command — the pinned variant
		// or the odysseus choice — not the base model's plain one, or they lose
		// the tuning (context window, KV cache type) that makes the model fit a
		// card in the first place.
		cmd, env, proxy := base.Cmd, base.Env, base.Proxy
		if target, pinned := pins[modelID]; pinned && target != "" {
			if v, ok := cfg.Models[target]; ok && strings.TrimSpace(v.Cmd) != "" {
				cmd, env, proxy = v.Cmd, v.Env, v.Proxy
			}
		} else if ch := choices[modelID]; ch != "" {
			if v, ok := cfg.Models[modelID+"--"+ch]; ok && strings.TrimSpace(v.Cmd) != "" {
				cmd, env, proxy = v.Cmd, v.Env, v.Proxy
			}
		}
		// The base entry is never launched (the selector rewrites the request
		// first), but it is what the detail card renders, so point it at the
		// same command the copies run.
		base.Cmd = cmd
		base.Env = env
		base.Proxy = proxy
		cfg.Models[modelID] = base

		parallel := parallelFromCmd(cmd)
		if parallel < 1 {
			parallel = 1
		}

		// Ports are allocated at config load, which happens before this
		// expansion, so a plain clone inherits the base's port and the second
		// copy cannot bind it and dies on startup. Give each copy its own port,
		// above every port already in use.
		nextPort := nextPortAfter(cfg)
		targets := make([]string, 0, len(cards))
		for i, card := range cards {
			copyID := fmt.Sprintf("%s--mm%d", modelID, i)
			cp := base
			cp.Cmd = cmd
			cp.Proxy = proxy
			if nextPort > 0 {
				cp = retargetPort(cp, nextPort)
				nextPort++
			}
			// Prefer this card, but keep the copy on single_gpu so the launch
			// policy can move it if the card is already taken.
			cp.Env = setEnvValue(env, "HIP_VISIBLE_DEVICES", card)
			cp.Unlisted = true
			cfg.Models[copyID] = cp
			modes[copyID] = ModeSingleGPU
			targets = append(targets, copyID)
		}

		// The base id fronts the copies: a request for it resolves through the
		// selector to a copy. The base model entry is KEPT, so the model still
		// appears in the UI and can still have its mode changed; only the copies
		// are (re)grouped. The selector rewrites the request to a copy before the
		// router runs, so the base's own process never starts.
		removeFromGroups(cfg, targets...)
		addToGroup(cfg, multiModelGroupID, targets...)

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

// nextPortAfter returns the first port above every port already assigned to a
// model, or 0 when no model uses an allocated port.
func nextPortAfter(cfg *config.Config) int {
	highest := 0
	for _, mc := range cfg.Models {
		if p := modelPort(mc); p > highest {
			highest = p
		}
	}
	if highest == 0 {
		return 0
	}
	return highest + 1
}

// modelPort returns the port a model's command — or, failing that, its proxy —
// is bound to. Zero means the model does not use an allocated port.
func modelPort(mc config.ModelConfig) int {
	toks := strings.Fields(mc.Cmd)
	for i, t := range toks {
		if t == "--port" && i+1 < len(toks) {
			if p, err := strconv.Atoi(toks[i+1]); err == nil {
				return p
			}
		}
		if strings.HasPrefix(t, "--port=") {
			if p, err := strconv.Atoi(strings.TrimPrefix(t, "--port=")); err == nil {
				return p
			}
		}
	}
	if u, err := url.Parse(mc.Proxy); err == nil && u.Port() != "" {
		if p, err := strconv.Atoi(u.Port()); err == nil {
			return p
		}
	}
	return 0
}

// retargetPort rewrites a model's command and proxy onto a new port.
func retargetPort(mc config.ModelConfig, port int) config.ModelConfig {
	old := modelPort(mc)
	if old == 0 || old == port {
		return mc
	}
	mc.Cmd = strings.ReplaceAll(mc.Cmd, fmt.Sprintf("--port %d", old), fmt.Sprintf("--port %d", port))
	mc.Cmd = strings.ReplaceAll(mc.Cmd, fmt.Sprintf("--port=%d", old), fmt.Sprintf("--port=%d", port))
	if u, err := url.Parse(mc.Proxy); err == nil {
		u.Host = net.JoinHostPort(u.Hostname(), strconv.Itoa(port))
		mc.Proxy = u.String()
	}
	return mc
}

// listDRMCards returns the ids of the cards, sorted, so copies get a
// deterministic card each.
func listDRMCards(root string) []string {
	ids := make([]string, 0, 2)
	for id := range readFreeVRAMGiB(root) {
		ids = append(ids, id)
	}
	if len(ids) == 0 {
		// Memory is unreadable on this driver or kernel, but the cards still
		// exist: fall back to the card nodes so copies are still generated.
		// The launch policy then skips its fit test (unknown free VRAM).
		entries, err := os.ReadDir(root)
		if err == nil {
			for _, e := range entries {
				if m := cardPattern.FindStringSubmatch(e.Name()); m != nil {
					ids = append(ids, m[1])
				}
			}
		}
	}
	sort.Strings(ids)
	return ids
}
