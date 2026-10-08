package router

import (
	"fmt"
	"slices"
	"strings"

	"github.com/mostlygeek/llama-swap/internal/config"
	"github.com/mostlygeek/llama-swap/internal/logmon"
	"github.com/mostlygeek/llama-swap/internal/process"
)

type Group struct {
	*baseRouter
}

func NewGroup(conf config.Config, proxylog, upstreamlog *logmon.Monitor) (*Group, error) {
	// Membership is resolved defensively. A group names models in configuration,
	// and that list drifts: models get added, removed, or rescanned out from
	// under it. A member with no model config has nothing to route, so skip it
	// with a warning instead of refusing to build the router, which would take
	// every other model down with it.
	modelToGroup := make(map[string]string)
	gids := make([]string, 0, len(conf.Routing.Router.Settings.Groups))
	for gid := range conf.Routing.Router.Settings.Groups {
		gids = append(gids, gid)
	}
	// Sorted so a model listed in two groups resolves to the same group on every
	// start rather than depending on map iteration order.
	slices.Sort(gids)

	for _, gid := range gids {
		for _, mid := range conf.Routing.Router.Settings.Groups[gid].Members {
			if _, _, found := conf.FindConfig(mid); !found {
				proxylog.Warnf("routing group %q lists model %q, which has no model config; ignoring it", gid, mid)
				continue
			}
			if existing, dup := modelToGroup[mid]; dup {
				return nil, fmt.Errorf("model %q is in multiple groups: %q and %q", mid, existing, gid)
			}
			modelToGroup[mid] = gid
		}
	}

	swapper := &groupSwapper{
		config:       conf,
		modelToGroup: modelToGroup,
	}

	processes := make(map[string]process.Process, len(modelToGroup))
	base, err := newBaseRouter("group", conf, processes, proxylog, swapper)
	if err != nil {
		return nil, fmt.Errorf("creating base router: %w", err)
	}

	for mid := range modelToGroup {
		modelCfg, _, ok := conf.FindConfig(mid)
		if !ok {
			// Unreachable: membership above only holds resolvable models. Skip
			// rather than fail, so a later edit here cannot take the whole
			// router down over one stale name.
			continue
		}
		procLog := logmon.NewWriter(upstreamlog)
		p, err := process.New(base.procCtx, mid, modelCfg, procLog, proxylog)
		if err != nil {
			base.shutdownFn()
			base.procCancel()
			return nil, fmt.Errorf("creating process for %q: %w", mid, err)
		}
		processes[mid] = p
	}

	g := &Group{baseRouter: base}
	go base.run()
	return g, nil
}

// groupSwapper decides evictions from static group configuration.
//
// Same-group siblings are stopped when the group has swap=true. Cross-group
// members are stopped only when the target's group is exclusive; loading a
// model from a non-exclusive group leaves running exclusive groups alone,
// matching the gotcha in the original ProcessGroup behaviour.
type groupSwapper struct {
	config       config.Config
	modelToGroup map[string]string
}

func (p *groupSwapper) EvictionFor(target string, running []string) []string {
	tg, _ := p.groupFor(target)
	tgCfg := p.config.Routing.Router.Settings.Groups[tg]

	seen := make(map[string]struct{})
	var result []string
	consider := func(mID string) {
		if mID == target {
			return
		}
		if _, dup := seen[mID]; dup {
			return
		}
		og, _ := p.groupFor(mID)
		switch {
		case og == tg && tgCfg.Swap:
			seen[mID] = struct{}{}
			result = append(result, mID)
		// the previous ProcessGroup behaviour did not unload exclusive groups
		// when loading a non-exclusive model. This maintains that gotcha
		// for backwards compatibility. The newer swap matrix approach does not
		// have this issue.
		case og != tg && tgCfg.Exclusive:
			if ogCfg := p.config.Routing.Router.Settings.Groups[og]; !ogCfg.Persistent {
				seen[mID] = struct{}{}
				result = append(result, mID)
			}
		}
	}

	for _, mID := range running {
		consider(mID)
	}
	return result
}

// groupFor returns the group that owns modelID.
//
// An exact membership wins. Otherwise a member covers every odysseus variant of
// itself -- models named "<member>--<label>" -- so a group only has to list the
// base model id to cover every generated variant of it. When members overlap,
// the longest matching member wins.
func (p *groupSwapper) groupFor(modelID string) (string, bool) {
	if gid, ok := p.modelToGroup[modelID]; ok {
		return gid, true
	}

	bestGID, bestLen := "", 0
	for member, gid := range p.modelToGroup {
		if len(member) <= bestLen || !strings.HasPrefix(modelID, member+"--") {
			continue
		}
		bestGID, bestLen = gid, len(member)
	}
	if bestLen == 0 {
		return "", false
	}
	return bestGID, true
}

func (p *groupSwapper) OnSwapStart(target string, running []string) {}
