package config

import "fmt"

// validatePool checks routing.router.settings.pool, fills in its defaults and
// resolves persistent aliases to real model IDs.
func validatePool(config *Config) error {
	pool := &config.Routing.Router.Settings.Pool

	if pool.VRAMGiB < 0 {
		return fmt.Errorf("routing.router.settings.pool.vramGiB must be >= 0")
	}
	switch pool.Placement {
	case "":
		pool.Placement = "auto"
	case "auto", "off":
	default:
		return fmt.Errorf("routing.router.settings.pool.placement: unknown value %q (valid: auto, off)", pool.Placement)
	}
	for i, c := range pool.Cards {
		if c <= 0 {
			return fmt.Errorf("routing.router.settings.pool.cards[%d] must be > 0", i)
		}
	}
	if len(pool.Cards) > 0 && pool.VRAMGiB != 0 {
		return fmt.Errorf("routing.router.settings.pool: set vramGiB or cards, not both")
	}
	if pool.ReserveGiB < 0 {
		return fmt.Errorf("routing.router.settings.pool.reserveGiB must be >= 0")
	}
	if pool.ReserveGiB == 0 {
		pool.ReserveGiB = PoolDefaultReserveGiB
	}
	if pool.ProtectSeconds < 0 {
		return fmt.Errorf("routing.router.settings.pool.protectSeconds must be >= 0")
	}
	if pool.ProtectSeconds == 0 {
		pool.ProtectSeconds = PoolDefaultProtectSeconds
	}

	sizes := make(map[string]float64, len(pool.Sizes))
	for id, gib := range pool.Sizes {
		real, found := config.RealModelName(id)
		if !found {
			return fmt.Errorf("routing.router.settings.pool.sizes references unknown model %q", id)
		}
		if gib <= 0 {
			return fmt.Errorf("routing.router.settings.pool.sizes.%s must be > 0", id)
		}
		sizes[real] = gib
	}
	pool.Sizes = sizes

	resolved := make([]string, 0, len(pool.Persistent))
	for _, id := range pool.Persistent {
		real, found := config.RealModelName(id)
		if !found {
			return fmt.Errorf("routing.router.settings.pool.persistent references unknown model %q", id)
		}
		resolved = append(resolved, real)
	}
	pool.Persistent = resolved

	af := &pool.AnswerFirst
	if af.MinPromptTokens < 0 || af.MaxWaitSeconds < 0 {
		return fmt.Errorf("routing.router.settings.pool.answerFirst: minPromptTokens and maxWaitSeconds must be >= 0")
	}
	if af.MinPromptTokens == 0 {
		af.MinPromptTokens = AnswerFirstDefaultMinPromptTokens
	}
	if af.MaxWaitSeconds == 0 {
		af.MaxWaitSeconds = AnswerFirstDefaultMaxWaitSeconds
	}
	models := make([]string, 0, len(af.Models))
	for _, id := range af.Models {
		real, found := config.RealModelName(id)
		if !found {
			return fmt.Errorf("routing.router.settings.pool.answerFirst.models references unknown model %q", id)
		}
		models = append(models, real)
	}
	af.Models = models

	serial := make([]string, 0, len(pool.OneAtATime))
	for _, id := range pool.OneAtATime {
		real, found := config.RealModelName(id)
		if !found {
			return fmt.Errorf("routing.router.settings.pool.oneAtATime references unknown model %q", id)
		}
		serial = append(serial, real)
	}
	pool.OneAtATime = serial

	cpu := make([]string, 0, len(pool.CPUFallback))
	for _, id := range pool.CPUFallback {
		real, found := config.RealModelName(id)
		if !found {
			return fmt.Errorf("routing.router.settings.pool.cpuFallback references unknown model %q", id)
		}
		cpu = append(cpu, real)
	}
	pool.CPUFallback = cpu

	sg := &pool.ShareGuard
	if sg.MaxSlowdownPercent < 0 || sg.MaxSlowdownPercent > 99 || sg.MaxWaitSeconds < 0 {
		return fmt.Errorf("routing.router.settings.pool.shareGuard: maxSlowdownPercent must be 0-99 and maxWaitSeconds >= 0")
	}
	if sg.MaxSlowdownPercent == 0 {
		sg.MaxSlowdownPercent = ShareGuardDefaultMaxSlowdownPercent
	}
	if sg.MaxWaitSeconds == 0 {
		sg.MaxWaitSeconds = ShareGuardDefaultMaxWaitSeconds
	}
	return nil
}
