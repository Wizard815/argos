package config

import "testing"

func TestConfig_ContextSizeFromCommand(t *testing.T) {
	tests := []struct {
		name string
		cmd  string
		want int
	}{
		{
			name: "short flag with separate value",
			cmd:  "llama-server -m /app/models/m.gguf -c 4096",
			want: 4096,
		},
		{
			name: "long flag with separate value",
			cmd:  "llama-server --ctx-size 262144 --flash-attn on",
			want: 262144,
		},
		{
			name: "long flag with equals value",
			cmd:  "llama-server --ctx-size=131072 -ngl 99",
			want: 131072,
		},
		{
			name: "env prefixed command",
			cmd:  "HSA_OVERRIDE_GFX_VERSION=9.0.6 HIP_VISIBLE_DEVICES=0,1 llama-server -c 256000 --host 0.0.0.0",
			want: 256000,
		},
		{
			name: "multiline generated command",
			cmd: `llama-server --host 0.0.0.0 --port ${PORT}
      -ngl 99 -c 262144 --flash-attn on
      --cache-type-k q8_0 --cache-type-v q8_0
      --fit off --split-mode layer --jinja
       -m /app/models/m.gguf`,
			want: 262144,
		},
		{
			name: "a prefix flag is not the context flag",
			cmd:  "llama-server -cram 51200 -m /app/models/m.gguf",
			want: 0,
		},
		{
			name: "no context flag at all",
			cmd:  "llama-server -m /app/models/m.gguf",
			want: 0,
		},
		{
			name: "flag without a value",
			cmd:  "llama-server -m /app/models/m.gguf -c",
			want: 0,
		},
		{
			name: "zero means take the window from the model",
			cmd:  "llama-server -c 0 -m /app/models/m.gguf",
			want: 0,
		},
		{
			name: "negative value",
			cmd:  "llama-server -c -1 -m /app/models/m.gguf",
			want: 0,
		},
		{
			name: "non llama.cpp server keeps its own -c",
			cmd:  "python3 /app/server.py -c /app/server.yaml",
			want: 0,
		},
		{
			name: "ollama style command",
			cmd:  "ollama serve",
			want: 0,
		},
		{
			name: "empty command",
			cmd:  "   ",
			want: 0,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if got := ContextSizeFromCommand(tt.cmd); got != tt.want {
				t.Errorf("ContextSizeFromCommand(%q) = %d, want %d", tt.cmd, got, tt.want)
			}
		})
	}
}
