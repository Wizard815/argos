package odysseus

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// TestOdysseus_FetchStateFromFile covers the file source that replaces the API
// path. Odysseus restricts the X-Odysseus-Internal-Token bypass to direct
// loopback clients (app.py:385, _is_trusted_loopback), so a llama-swap in
// another container cannot authenticate to /api/cookbook/state at all -- it gets
// 401 no matter how correct the token is. Reading the file needs no credential.
func TestOdysseus_FetchStateFromFile(t *testing.T) {
	dir := t.TempDir()
	stateFile := filepath.Join(dir, "cookbook_state.json")
	write(t, stateFile, `{
  "tasks": [
    {
      "id": "sess-1",
      "modelId": "unsloth/Qwen3.6-35B-A3B-MTP-GGUF",
      "status": "running",
      "ts": 2000,
      "payload": {"repo_id": "unsloth/Qwen3.6-35B-A3B-MTP-GGUF", "_cmd": `+jsonString(cmdMTP)+`}
    }
  ]
}`)

	// no BaseURL, no token -- exactly the configuration a stateFile allows
	opts := Options{StateFile: stateFile, EnsureDio: true}

	st, err := FetchState(context.Background(), opts)
	if err != nil {
		t.Fatalf("FetchState from file: %v", err)
	}
	if len(st.Tasks) != 1 {
		t.Fatalf("got %d tasks, want 1", len(st.Tasks))
	}
	if st.Tasks[0].Payload.Cmd != cmdMTP {
		t.Errorf("task command did not round-trip through the file")
	}

	// and it has to produce the same result the API path would
	res, err := Build(st, opts)
	if err != nil {
		t.Fatal(err)
	}
	if _, ok := res.Models["qwen3.6-35b-a3b-ud-q6_k_xl--ody"]; !ok {
		t.Errorf("variant missing; have %v", keys(res.Models))
	}
}

// TestOdysseus_FetchStateFromFile_Missing keeps the error message useful when
// the mount isn't there.
func TestOdysseus_FetchStateFromFile_Missing(t *testing.T) {
	_, err := FetchState(context.Background(), Options{StateFile: "/nonexistent/cookbook_state.json"})
	if err == nil {
		t.Fatal("expected an error for a missing state file")
	}
	if !strings.Contains(err.Error(), "cookbook_state.json") {
		t.Errorf("error should name the file: %v", err)
	}
}

// TestOdysseus_FetchStateFromFile_Malformed rejects a bad file rather than
// silently producing an empty profile set.
func TestOdysseus_FetchStateFromFile_Malformed(t *testing.T) {
	dir := t.TempDir()
	bad := filepath.Join(dir, "cookbook_state.json")
	if err := os.WriteFile(bad, []byte("{not json"), 0o644); err != nil {
		t.Fatal(err)
	}
	if _, err := FetchState(context.Background(), Options{StateFile: bad}); err == nil {
		t.Fatal("expected an error for malformed JSON")
	}
}

// jsonString encodes a Go string as a JSON string literal, so the fixture's
// multi-line command stays valid JSON.
func jsonString(s string) string {
	b, err := json.Marshal(s)
	if err != nil {
		panic(err)
	}
	return string(b)
}
