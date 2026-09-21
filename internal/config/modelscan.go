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
	// A group with its own Dirs pointed at an embedding-models folder and
	// its own CmdTemplate (or macro) including --embedding solves this
	// without touching the primary chat-model scan at all.
	//
	// Each group's OutputFile must be distinct from the primary OutputFile
	// and every other group's, since each is written independently.
	Groups []ModelScanGroup `yaml:"groups"`
}

// ModelScanGroup is one additional scan target under ModelScanConfig.Groups.
// It mirrors the primary target's fields exactly, scanned and written
// independently via its own OutputFile.
type ModelScanGroup struct {
	// Dirs are the directories to scan, recursively, for model files.
	Dirs []string `yaml:"dirs"`

	// Extensions are the file extensions to treat as models. Defaults to
	// [".gguf"] when empty.
	Extensions []string `yaml:"extensions"`

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
