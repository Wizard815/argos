package process

import "sync/atomic"

// LaunchEnvPolicy, when set, rewrites a model's launch environment just before
// the upstream command is started.
//
// It exists so a higher layer can apply runtime decisions that depend on live
// state — for example binding a model to whichever GPU currently has enough
// free VRAM — without the process package having to know anything about GPUs,
// modes or hardware. argv is the already-expanded command, so a policy can read
// flags such as -m from it; env is the model's configured environment.
//
// Returning a nil slice means "no change"; the caller falls back to the
// configured env.
type LaunchEnvPolicy func(modelID string, argv, env []string) []string

var launchEnvPolicy atomic.Pointer[LaunchEnvPolicy]

// SetLaunchEnvPolicy installs the policy. A nil policy restores the default
// behaviour (the configured env is used verbatim). Called once per Server, so a
// hot config reload simply replaces the previous policy.
func SetLaunchEnvPolicy(fn LaunchEnvPolicy) {
	if fn == nil {
		launchEnvPolicy.Store(nil)
		return
	}
	launchEnvPolicy.Store(&fn)
}

// applyLaunchEnvPolicy runs the installed policy, if any.
func applyLaunchEnvPolicy(modelID string, argv, env []string) []string {
	p := launchEnvPolicy.Load()
	if p == nil {
		return env
	}
	out := (*p)(modelID, argv, env)
	if out == nil {
		return env
	}
	return out
}
