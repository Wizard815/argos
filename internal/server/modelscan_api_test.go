package server

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/mostlygeek/llama-swap/internal/config"
)

// TestServer_RunModelScan_Groups verifies that runModelScan scans the
// primary target and every entry under ModelScan.Groups independently,
// each writing to its own OutputFile — the mechanism that lets one
// modelScan config serve both chat models (one CmdTemplate) and embedding
// models (a different CmdTemplate, e.g. with --embedding) from separate
// directories without either template being applied to the wrong models.
func TestServer_RunModelScan_Groups(t *testing.T) {
	dir := t.TempDir()

	chatDir := filepath.Join(dir, "chat")
	embedDir := filepath.Join(dir, "embed")
	if err := os.MkdirAll(chatDir, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(embedDir, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(chatDir, "gemma.gguf"), []byte("x"), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(embedDir, "nomic-embed-text-v1.5.gguf"), []byte("x"), 0o644); err != nil {
		t.Fatal(err)
	}

	chatOut := filepath.Join(dir, "models.chat.generated.yaml")
	embedOut := filepath.Join(dir, "models.embeddings.generated.yaml")

	s := &Server{
		cfg: config.Config{
			ModelScan: config.ModelScanConfig{
				Enabled:     true,
				Dirs:        []string{chatDir},
				CmdTemplate: "llama-server -m ${MODEL_PATH} --port ${PORT}",
				OutputFile:  chatOut,
				Groups: []config.ModelScanGroup{
					{
						Dirs:        []string{embedDir},
						CmdTemplate: "llama-server -m ${MODEL_PATH} --port ${PORT} --embedding --pooling mean",
						NamePrefix:  "embed-",
						OutputFile:  embedOut,
					},
				},
			},
		},
	}

	wrote, count, err := s.runModelScan()
	if err != nil {
		t.Fatalf("runModelScan() error = %v", err)
	}
	if !wrote {
		t.Errorf("runModelScan() wrote = false, want true (both output files are new)")
	}
	if count != 2 {
		t.Errorf("runModelScan() count = %d, want 2 (1 chat + 1 embedding model)", count)
	}

	chatData, err := os.ReadFile(chatOut)
	if err != nil {
		t.Fatalf("reading chat output: %v", err)
	}
	if !strings.Contains(string(chatData), "gemma") {
		t.Errorf("chat output missing gemma model:\n%s", chatData)
	}
	if strings.Contains(string(chatData), "embed") {
		t.Errorf("chat output should not contain embedding model:\n%s", chatData)
	}

	embedData, err := os.ReadFile(embedOut)
	if err != nil {
		t.Fatalf("reading embedding output: %v", err)
	}
	if !strings.Contains(string(embedData), "--embedding") {
		t.Errorf("embedding output missing --embedding flag:\n%s", embedData)
	}
	if !strings.Contains(string(embedData), "embed-nomic-embed-text-v1.5") {
		t.Errorf("embedding output missing prefixed model id:\n%s", embedData)
	}

	// A second scan with nothing changed on disk must not report a write —
	// runModelScan is called on every /v1/models request, and a spurious
	// write triggers -watch-config to reload and restart already-running
	// model processes.
	wrote, _, err = s.runModelScan()
	if err != nil {
		t.Fatalf("second runModelScan() error = %v", err)
	}
	if wrote {
		t.Errorf("second runModelScan() wrote = true, want false (nothing changed on disk)")
	}
}

// TestServer_RunModelScan_GroupSharesDirsWithMatch covers the real-world
// case that motivated Match: an embedding GGUF living inside the SAME
// directory tree as chat models (e.g. a shared HF-cache download folder),
// rather than its own separate folder. The group's Match pattern must claim
// only the embedding file, and the primary scan must automatically exclude
// anything a group's Match claims — otherwise the same file would be
// registered twice, once correctly (with --embedding) and once wrongly
// (launched with the chat CmdTemplate, which never serves /v1/embeddings).
func TestServer_RunModelScan_GroupSharesDirsWithMatch(t *testing.T) {
	dir := t.TempDir()
	modelsDir := filepath.Join(dir, "models", "HFCache", "hub")
	if err := os.MkdirAll(modelsDir, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(modelsDir, "gemma-4-12b.gguf"), []byte("x"), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(modelsDir, "nomic-embed-text-v2-moe.Q8_0.gguf"), []byte("x"), 0o644); err != nil {
		t.Fatal(err)
	}

	chatOut := filepath.Join(dir, "models.generated.yaml")
	embedOut := filepath.Join(dir, "models.embeddings.generated.yaml")

	s := &Server{
		cfg: config.Config{
			ModelScan: config.ModelScanConfig{
				Enabled:     true,
				Dirs:        []string{filepath.Join(dir, "models")},
				CmdTemplate: "llama-server -m ${MODEL_PATH} --port ${PORT}",
				OutputFile:  chatOut,
				Groups: []config.ModelScanGroup{
					{
						Dirs:        []string{filepath.Join(dir, "models")},
						Match:       "embed",
						CmdTemplate: "llama-server -m ${MODEL_PATH} --port ${PORT} --embedding --pooling mean",
						OutputFile:  embedOut,
					},
				},
			},
		},
	}

	_, count, err := s.runModelScan()
	if err != nil {
		t.Fatalf("runModelScan() error = %v", err)
	}
	if count != 2 {
		t.Errorf("runModelScan() count = %d, want 2 (1 chat + 1 embedding model)", count)
	}

	chatData, err := os.ReadFile(chatOut)
	if err != nil {
		t.Fatalf("reading chat output: %v", err)
	}
	if !strings.Contains(string(chatData), "gemma-4-12b") {
		t.Errorf("chat output missing gemma model:\n%s", chatData)
	}
	if strings.Contains(string(chatData), "nomic-embed") {
		t.Errorf("chat output should exclude the embedding model (claimed by the group's Match):\n%s", chatData)
	}

	embedData, err := os.ReadFile(embedOut)
	if err != nil {
		t.Fatalf("reading embedding output: %v", err)
	}
	if !strings.Contains(string(embedData), "--embedding") {
		t.Errorf("embedding output missing --embedding flag:\n%s", embedData)
	}
	if strings.Contains(string(embedData), "gemma") {
		t.Errorf("embedding output should not contain the chat model:\n%s", embedData)
	}
}
