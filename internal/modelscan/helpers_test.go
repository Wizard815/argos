package modelscan

import (
	"bytes"
	"encoding/binary"
	"os"
	"path/filepath"
	"sort"
	"testing"
)

// writeGGUF writes a GGUF header with general.architecture, <arch>.block_count,
// a string array and a tokenizer key, enough for readGGUFArch.
func writeGGUF(t *testing.T, dir, name, arch string, layers uint32) {
	t.Helper()
	var b bytes.Buffer
	le := func(v any) { _ = binary.Write(&b, binary.LittleEndian, v) }
	str := func(s string) { le(uint64(len(s))); b.WriteString(s) }
	b.WriteString("GGUF")
	le(uint32(3))
	le(uint64(0)) // tensors
	le(uint64(5)) // kv pairs
	str("general.architecture")
	le(uint32(8))
	str(arch)
	str("general.tags")
	le(uint32(9))
	le(uint32(8))
	le(uint64(2))
	str("a")
	str("bb")
	str("general.file_type")
	le(uint32(4))
	le(uint32(7))
	str(arch + ".block_count")
	le(uint32(4))
	le(layers)
	str("tokenizer.ggml.model")
	le(uint32(8))
	str("gpt2")
	if err := os.WriteFile(filepath.Join(dir, name), b.Bytes(), 0o644); err != nil {
		t.Fatal(err)
	}
}

func TestScan_SkipsProjectorsAndMTPHeads(t *testing.T) {
	dir := t.TempDir()
	writeGGUF(t, dir, "Qwen3-Coder-Next-Q5_K_S.gguf", "qwen3next", 48)
	writeGGUF(t, dir, "Qwen3-Coder-Next-MTP-Q8_0.gguf", "qwen3next", 1) // MTP head
	writeGGUF(t, dir, "GLM-4.6-MTP-Q4_K_M.gguf", "glm4moe", 93)         // MTP built in: a model
	writeGGUF(t, dir, "gemma-3-vision-projector-f16.gguf", "clip", 27)  // projector without "mmproj" in its name
	writeFakeModel(t, dir, "mmproj-F16.gguf")
	writeFakeModel(t, dir, "notes-mtp.gguf") // not a GGUF: kept, as before

	_, names, err := Scan(Options{Dirs: []string{dir}, CmdTemplate: "llama-server -m ${MODEL_PATH}"})
	if err != nil {
		t.Fatal(err)
	}
	sort.Strings(names)
	want := []string{"glm-4.6-mtp-q4_k_m", "notes-mtp", "qwen3-coder-next-q5_k_s"}
	if len(names) != len(want) {
		t.Fatalf("models = %v, want %v", names, want)
	}
	for i := range want {
		if names[i] != want[i] {
			t.Fatalf("models = %v, want %v", names, want)
		}
	}
}

func TestScan_ReadGGUFArch(t *testing.T) {
	dir := t.TempDir()
	writeGGUF(t, dir, "m.gguf", "llama", 32)
	arch, layers, err := readGGUFArch(filepath.Join(dir, "m.gguf"))
	if err != nil || arch != "llama" || layers != 32 {
		t.Fatalf("got %q %d %v", arch, layers, err)
	}
	if _, _, err := readGGUFArch(writeFakeModel(t, dir, "x.gguf")); err == nil {
		t.Fatal("a non-GGUF file read without an error")
	}
}
