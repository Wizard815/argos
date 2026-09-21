package server

import (
	"encoding/json"
	"net/http"
	"strings"

	"github.com/mostlygeek/llama-swap/internal/modelscan"
)

// runModelScan executes a directory scan per s.cfg.ModelScan and writes the
// generated models fragment if its content changed. It is a no-op (ok=true,
// wrote=false, count=0) when ModelScan.Enabled is false, so callers can call
// it unconditionally.
//
// A write does not reload this Server — it relies on the existing
// -watch-config file watcher (internal/config, wired in llama-swap.go) to
// notice the change and hot-reload, same as any other hand edit to a file
// under -config-dir. Without -watch-config, writes here still happen but
// nothing picks them up until the process is restarted or another reload
// trigger (e.g. SIGHUP) fires.
func (s *Server) runModelScan() (wrote bool, count int, err error) {
	cfg := s.cfg.ModelScan
	if !cfg.Enabled {
		return false, 0, nil
	}

	// Primary target, plus one additional Scan+write per group (e.g. a
	// --embedding CmdTemplate for embedding GGUFs) — each group is
	// independent, so one group's error does not prevent the others (or the
	// primary target) from being written.
	//
	// A group's own Match pattern is what it uses to claim files as its
	// own; those same patterns are collected here and excluded from the
	// primary scan, so a group sharing Dirs with the primary target (e.g.
	// an embedding GGUF sitting in the same HF-cache folder as chat models)
	// doesn't also get picked up by the primary scan and launched with the
	// wrong CmdTemplate.
	groupMatches := make([]string, 0, len(cfg.Groups))
	for _, g := range cfg.Groups {
		if strings.TrimSpace(g.Match) != "" {
			groupMatches = append(groupMatches, g.Match)
		}
	}

	targets := make([]modelscan.Options, 0, 1+len(cfg.Groups))
	targets = append(targets, modelscan.Options{
		Dirs:            cfg.Dirs,
		Extensions:      cfg.Extensions,
		CmdTemplate:     cfg.CmdTemplate,
		NamePrefix:      cfg.NamePrefix,
		OutputPath:      cfg.OutputFile,
		ExcludePatterns: groupMatches,
	})
	for _, g := range cfg.Groups {
		targets = append(targets, modelscan.Options{
			Dirs:        g.Dirs,
			Extensions:  g.Extensions,
			CmdTemplate: g.CmdTemplate,
			NamePrefix:  g.NamePrefix,
			OutputPath:  g.OutputFile,
			NamePattern: g.Match,
		})
	}

	var firstErr error
	for _, opts := range targets {
		data, names, scanErr := modelscan.Scan(opts)
		if scanErr != nil {
			if firstErr == nil {
				firstErr = scanErr
			}
			continue
		}

		w, writeErr := modelscan.WriteIfChanged(opts.OutputPath, data)
		if writeErr != nil {
			if firstErr == nil {
				firstErr = writeErr
			}
			continue
		}
		wrote = wrote || w
		count += len(names)
	}

	return wrote, count, firstErr
}

// handleAPIRescanModels is the explicit, synchronous trigger: POST
// /api/models/rescan. It scans immediately and reports what it found, so a
// caller (e.g. a button in another app) gets a direct answer rather than
// having to poll /v1/models and guess whether a scan happened.
func (s *Server) handleAPIRescanModels(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")

	if !s.cfg.ModelScan.Enabled {
		w.WriteHeader(http.StatusServiceUnavailable)
		json.NewEncoder(w).Encode(map[string]any{
			"ok":    false,
			"error": "modelScan is not enabled in config",
		})
		return
	}

	wrote, count, err := s.runModelScan()
	if err != nil {
		s.proxylog.Warnf("modelscan: rescan failed: %v", err)
		w.WriteHeader(http.StatusInternalServerError)
		json.NewEncoder(w).Encode(map[string]any{
			"ok":    false,
			"error": err.Error(),
		})
		return
	}

	s.proxylog.Infof("modelscan: rescan found %d models, config changed=%v", count, wrote)
	json.NewEncoder(w).Encode(map[string]any{
		"ok":      true,
		"models":  count,
		"changed": wrote,
	})
}

// triggerBackgroundModelScan runs a scan in the background, logging any
// error rather than surfacing one to the caller. Used by handleListModels
// so that GET /v1/models — the endpoint every OpenAI-compatible client
// (including a "Refresh models" button) already calls — also keeps the
// generated models fragment current, without making that request slower or
// fail because of a scan problem. Because runModelScan only writes when
// content actually changed, calling this on every /v1/models hit does not
// cause needless reloads (and therefore does not restart already-loaded
// models) when the directory hasn't changed since the last scan.
func (s *Server) triggerBackgroundModelScan() {
	if !s.cfg.ModelScan.Enabled {
		return
	}
	go func() {
		wrote, count, err := s.runModelScan()
		if err != nil {
			s.proxylog.Warnf("modelscan: background scan failed: %v", err)
			return
		}
		if wrote {
			s.proxylog.Infof("modelscan: background scan found %d models, config changed", count)
		}
	}()
}
