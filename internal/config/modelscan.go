package config

// ModelScanConfig configures directory-based model auto-discovery: scanning
// one or more directories for model files and generating a `models:` YAML
// fragment from them, instead of hand-listing every model in config.yaml.
//
// This is a local fork addition (see internal/modelscan), not part of
// upstream llama-swap.
type ModelScanConfig struct {
	// Enabled turns on the /api/models/rescan endpoint and the automatic
	// background scan triggered by GET /v1/models. Defaults to false so
	// existing configs are unaffected.
	Enabled bool `yaml:"enabled"`

	// Dirs are the directories to scan, recursively, for model files.
	Dirs []string `yaml:"dirs"`

	// Extensions are the file extensions to treat as models. Defaults to
	// [".gguf"] when empty.
	Extensions []string `yaml:"extensions"`

	// CmdTemplate is the ModelConfig.Cmd to use for every discovered model.
	// The literal substring "${MODEL_PATH}" is replaced with the model's
	// absolute file path; any llama-swap macro (e.g. "${PORT}", or a name
	// defined under the top-level macros: block) is left untouched and
	// resolved normally by llama-swap at process-launch time.
	CmdTemplate string `yaml:"cmdTemplate"`

	// NamePrefix is prepended to every generated model ID, e.g. "local-".
	NamePrefix string `yaml:"namePrefix"`

	// OutputFile is the generated YAML fragment's path. It must live inside
	// the directory passed as -config-dir so llama-swap's config-dir merge
	// (and, with -watch-config, its file watcher) picks it up.
	OutputFile string `yaml:"outputFile"`

	// Groups are additional scan targets, each with its own Dirs/CmdTemplate/
	// NamePrefix/OutputFile, scanned alongside the primary target above.
	//
	// This exists because one CmdTemplate cannot serve every kind of model:
	// an embedding-only GGUF needs llama-server's --embedding flag (and
	// usually a --pooling mode), which would break a normal chat model if
	// applied to it, and vice versa — a chat CmdTemplate omits --embedding,
	// so an embedding GGUF launched with it never serves /v1/embeddings.
	// A group with its own CmdTemplate (or macro) including --embedding
	// solves this without touching the primary chat-model scan at all —
	// either by pointing its Dirs at a separate embedding-models folder, or,
	// when embeddings live inside the same directory tree as chat models
	// (e.g. a shared HF-cache download folder), by sharing Dirs with the
	// primary scan and using Match to pick out just the embedding files.
	//
	// Each group's OutputFile must be distinct from the primary OutputFile
	// and every other group's, since each is written independently.
	Groups []ModelScanGroup `yaml:"groups"`
}

// ModelScanGroup is one additional scan target under ModelScanConfig.Groups.
// It mirrors the primary target's fields, scanned and written independently
// via its own OutputFile.
type ModelScanGroup struct {
	// Dirs are the directories to scan, recursively, for model files. May be
	// the same directories as the primary target's (or another group's) —
	// Match (below) is what actually separates which files land in which
	// group when they share a directory tree, e.g. an embedding GGUF sitting
	// in the same HF-cache folder as chat models.
	Dirs []string `yaml:"dirs"`

	// Extensions are the file extensions to treat as models. Defaults to
	// [".gguf"] when empty.
	Extensions []string `yaml:"extensions"`

	// Match is a case-insensitive regular expression tested against each
	// discovered file's full path. When set, only matching files are
	// included in this group — and, since a group's models are meant to run
	// with a CmdTemplate the primary scan's models must NOT get (e.g.
	// --embedding), any file matched by a group's Match is automatically
	// excluded from the primary scan too, even when they share Dirs. Leave
	// empty to include every file found in Dirs (only useful when this
	// group's Dirs don't overlap the primary scan's or another group's).
	Match string `yaml:"match"`

	// CmdTemplate is the ModelConfig.Cmd to use for every model discovered
	// in this group. See ModelScanConfig.CmdTemplate for macro substitution
	// rules.
	CmdTemplate string `yaml:"cmdTemplate"`

	// NamePrefix is prepended to every generated model ID in this group.
	NamePrefix string `yaml:"namePrefix"`

	// OutputFile is the generated YAML fragment's path for this group. Must
	// be distinct from the primary OutputFile and every other group's.
	OutputFile string `yaml:"outputFile"`
}
