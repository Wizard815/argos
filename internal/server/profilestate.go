package server

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
)

// The active profile is runtime state: the server starts with no profile active
// unless something says otherwise, and a container recreate or a configuration
// reload therefore drops the operator's selection. profileStateFile records it,
// so a restart comes back on the profile that was last in use instead of "none".

// profileState is the on-disk shape of the remembered selection. An empty
// Active is meaningful: the operator last chose "no profile", and that has to
// survive a restart too, or a deactivation would resurrect the startup hook.
type profileState struct {
	Active string `json:"active"`
}

// loadProfileState reads the remembered selection. The bool reports whether a
// state file was found at all, which is how a first run is told apart from a
// remembered "none".
func loadProfileState(path string) (string, bool, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		if os.IsNotExist(err) {
			return "", false, nil
		}
		return "", false, err
	}

	var st profileState
	if err := json.Unmarshal(data, &st); err != nil {
		return "", true, fmt.Errorf("parsing %s: %w", path, err)
	}
	return st.Active, true, nil
}

// saveProfileState writes the selection atomically: a half-written file must
// never be what the next start reads back, and the write has to work when the
// file is bind-mounted into a container.
func saveProfileState(path, active string) error {
	data, err := json.MarshalIndent(profileState{Active: active}, "", "  ")
	if err != nil {
		return err
	}

	if dir := filepath.Dir(path); dir != "" && dir != "." {
		if err := os.MkdirAll(dir, 0o755); err != nil {
			return fmt.Errorf("creating profile state dir: %w", err)
		}
	}

	tmp := path + ".tmp"
	if err := os.WriteFile(tmp, append(data, '\n'), 0o644); err != nil {
		return fmt.Errorf("writing %s: %w", path, err)
	}
	return os.Rename(tmp, path)
}

// restoreProfileState applies the remembered profile during startup.
//
// Precedence: a remembered name wins over hooks.on_startup.profile, because it
// is what the operator last picked in the UI. The hook is the fallback for a
// first run, for a deleted state file, and for a remembered name that is no
// longer configured - a stale name is a warning, never a failed start. Whatever
// is applied in that fallback path is written back, so the file exists from the
// first boot onward.
func (s *Server) restoreProfileState() {
	path := s.cfg.ProfileStateFile
	if path == "" {
		return
	}

	name, found, err := loadProfileState(path)
	if err != nil {
		s.proxylog.Warnf("profile state: %v", err)
		found = false
	}

	if found && name != "" {
		if _, ok := s.cfg.Profiles[name]; !ok {
			s.proxylog.Warnf("profile state: %s remembers profile %q which is not configured, using hooks.on_startup.profile", path, name)
			found = false
		}
	}

	if found {
		s.profileMu.Lock()
		s.activeProfile = name
		s.profileMu.Unlock()
		if name == "" {
			s.proxylog.Infof("profile state: %s remembers no active profile", path)
		} else {
			s.proxylog.Infof("profile state: restored active profile %q from %s", name, path)
		}
		return
	}

	name = s.cfg.Hooks.OnStartup.Profile
	s.profileMu.Lock()
	s.activeProfile = name
	s.profileMu.Unlock()

	if name != "" {
		s.proxylog.Infof("profile state: starting on hooks.on_startup.profile %q", name)
	}
	if err := saveProfileState(path, name); err != nil {
		s.proxylog.Warnf("profile state: %v", err)
	}
}
