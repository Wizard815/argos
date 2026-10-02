package process

import "testing"

func TestProcess_LaunchEnvPolicyDefaultPassthrough(t *testing.T) {
	SetLaunchEnvPolicy(nil)
	env := []string{"A=1"}
	got := applyLaunchEnvPolicy("m", []string{"llama-server"}, env)
	if len(got) != 1 || got[0] != "A=1" {
		t.Errorf("no policy should pass the env through, got %v", got)
	}
}

func TestProcess_LaunchEnvPolicyRewritesEnv(t *testing.T) {
	t.Cleanup(func() { SetLaunchEnvPolicy(nil) })
	SetLaunchEnvPolicy(func(modelID string, argv, env []string) []string {
		return append(append([]string{}, env...), "HIP_VISIBLE_DEVICES="+modelID)
	})

	got := applyLaunchEnvPolicy("gpu1", nil, []string{"A=1"})
	want := map[string]bool{"A=1": true, "HIP_VISIBLE_DEVICES=gpu1": true}
	if len(got) != 2 || !want[got[0]] || !want[got[1]] {
		t.Errorf("policy rewrite not applied, got %v", got)
	}
}

func TestProcess_LaunchEnvPolicyNilMeansNoChange(t *testing.T) {
	t.Cleanup(func() { SetLaunchEnvPolicy(nil) })
	SetLaunchEnvPolicy(func(string, []string, []string) []string { return nil })

	got := applyLaunchEnvPolicy("m", nil, []string{"A=1"})
	if len(got) != 1 || got[0] != "A=1" {
		t.Errorf("a nil result must fall back to the configured env, got %v", got)
	}
}

func TestProcess_LaunchEnvPolicySeesExpandedArgv(t *testing.T) {
	t.Cleanup(func() { SetLaunchEnvPolicy(nil) })
	var seen []string
	SetLaunchEnvPolicy(func(_ string, argv, env []string) []string {
		seen = argv
		return env
	})

	applyLaunchEnvPolicy("m", []string{"llama-server", "-m", "/x.gguf"}, nil)
	if len(seen) != 3 || seen[0] != "llama-server" || seen[1] != "-m" {
		t.Errorf("policy should receive the expanded argv, saw %v", seen)
	}
}
