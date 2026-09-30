package odysseus

import (
	"strings"
	"testing"
)

// A variable written both as an export line and as an inline prefix must reach
// llama-swap once: the duplicate showed up verbatim in the generated fragment,
// as two identical HIP_VISIBLE_DEVICES entries.
func TestParseCommand_DeduplicatesEnvWrittenTwice(t *testing.T) {
	cmd := `export HIP_VISIBLE_DEVICES=0,1
export GGML_ENABLE_CUSTOM_AR=1
HIP_VISIBLE_DEVICES=0,1 llama-server --model /app/models/x.gguf --port 8000 -ngl 99`

	p, err := ParseCommand(cmd, true)
	if err != nil {
		t.Fatalf("ParseCommand: %v", err)
	}

	count := 0
	for _, e := range p.Env {
		if strings.HasPrefix(e, "HIP_VISIBLE_DEVICES=") {
			count++
		}
	}
	if count != 1 {
		t.Errorf("HIP_VISIBLE_DEVICES appears %d times, want 1: %v", count, p.Env)
	}
}

// The inline assignment is the later one, so it wins, exactly as a shell would
// resolve the same command.
func TestParseCommand_InlineEnvWinsOverExport(t *testing.T) {
	cmd := `export HIP_VISIBLE_DEVICES=0
HIP_VISIBLE_DEVICES=1 llama-server --model /app/models/x.gguf --port 8000 -ngl 99`

	p, err := ParseCommand(cmd, true)
	if err != nil {
		t.Fatalf("ParseCommand: %v", err)
	}

	got := ""
	for _, e := range p.Env {
		if strings.HasPrefix(e, "HIP_VISIBLE_DEVICES=") {
			got = e
		}
	}
	if got != "HIP_VISIBLE_DEVICES=1" {
		t.Errorf("HIP_VISIBLE_DEVICES = %q, want the inline assignment to win", got)
	}
}
