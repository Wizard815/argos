package server

import (
	"sort"

	"github.com/Wizard815/Argos/internal/modelctl"
)

// The modelctl provider reports Argos's own model and profile state over MCP.
// This is the adapter that exposes the running server to it: the provider
// depends on an interface, and these are the methods that satisfy it.
//
// The methods read live state rather than a snapshot, so a model that loads
// between two calls shows up on the second one.
var _ modelctl.State = (*Server)(nil)

// Profiles implements modelctl.State. Names are sorted so the listing is
// stable across calls, which matters because a model reads it as text.
func (s *Server) Profiles() []modelctl.ProfileInfo {
	out := make([]modelctl.ProfileInfo, 0, len(s.cfg.Profiles))
	for name, profile := range s.cfg.Profiles {
		out = append(out, modelctl.ProfileInfo{Name: name, Description: profile.Description})
	}
	sort.Slice(out, func(i, j int) bool { return out[i].Name < out[j].Name })
	return out
}

// SetActiveProfile implements modelctl.State. An empty name clears the
// selection; an unknown name is an error and changes nothing. ActiveProfile
// already exists on Server and satisfies the interface on its own.
func (s *Server) SetActiveProfile(name string) error {
	_, err := s.setActiveProfile(name)
	return err
}

// Models implements modelctl.State. Running state is joined from the process
// supervisor, so a model that is loaded is reported as loaded even when it was
// started by a request rather than by a preload.
func (s *Server) Models() []modelctl.ModelInfo {
	running := s.RunningModels()
	out := make([]modelctl.ModelInfo, 0, len(s.cfg.Models))
	for id, mc := range s.cfg.Models {
		_, isRunning := running[id]
		out = append(out, modelctl.ModelInfo{
			ID:          id,
			Name:        mc.Name,
			Description: mc.Description,
			Proxy:       mc.Proxy,
			TTL:         mc.UnloadAfter,
			Running:     isRunning,
		})
	}
	sort.Slice(out, func(i, j int) bool { return out[i].ID < out[j].ID })
	return out
}

// UnloadAll implements modelctl.State. The zero timeout means "do not wait", so
// an unload request from a model does not block on a slow shutdown.
func (s *Server) UnloadAll() {
	if s.local == nil {
		return
	}
	s.local.Unload(0)
}
