package config

import (
	"reflect"
	"strings"
	"testing"
)

const gemmaTensorMTP = "/app/bin/remote-llama rocm_srv3 --model /m/Gemma4-26B-Q4_K_M.gguf -ngl 99 -c 131072 --flash-attn on " +
	"--cache-type-k q8_0 --cache-type-v q8_0 -md /m/mtp-gemma-4-26B-A4B-it.gguf --spec-type draft-mtp " +
	"--spec-draft-n-max 3 --split-mode tensor"

func companion(t *testing.T, cmd string) []string {
	t.Helper()
	args, err := SanitizeCommand(cmd)
	if err != nil {
		t.Fatal(err)
	}
	return CompanionFlags(args)
}

func TestCompanionFlags_TensorAndMTPDraftGetsDraftOnOneCard(t *testing.T) {
	want := []string{"--device-draft", "ROCm0"}
	if got := companion(t, gemmaTensorMTP); !reflect.DeepEqual(got, want) {
		t.Fatalf("got %v, want %v", got, want)
	}
}

func TestCompanionFlags_BuiltInMTPLayersAndEqualsSpellingCount(t *testing.T) {
	got := companion(t, "/app/bin/remote-llama rocm --model /m/Qwen3.8-27B-MTP-Q8_0.gguf --spec-type=draft-mtp -sm=tensor -ctk q4_0 -ctv q4_0")
	if len(got) != 2 || got[1] != "ROCm0" {
		t.Fatalf("got %v", got)
	}
}

func TestCompanionFlags_WhatTheCommandSetsWins(t *testing.T) {
	for _, own := range []string{" -devd ROCm1", " --device-draft=ROCm1"} {
		if got := companion(t, gemmaTensorMTP+own); got != nil {
			t.Fatalf("%q set by hand, nothing to add: got %v", own, got)
		}
	}
}

func TestCompanionFlags_NotNeededIsNotAdded(t *testing.T) {
	for _, cmd := range []string{
		strings.Replace(gemmaTensorMTP, "--split-mode tensor", "--split-mode layer", 1),
		"llama-server -m /m/a.gguf -sm tensor -md /m/small-draft.gguf --spec-type draft",
		"llama-server -m /m/a.gguf -c 8192",
	} {
		if got := companion(t, cmd); got != nil {
			t.Fatalf("%q: got %v", cmd, got)
		}
	}
	if got := companion(t, gemmaTensorMTP); len(got) != 2 {
		t.Fatalf("no draft cache type is ever added (Gemma 4's draft shares the main cache): got %v", got)
	}
}

func TestCompanionFlags_DeviceFromListRuntimeOrHost(t *testing.T) {
	if got := companion(t, gemmaTensorMTP+" --device ROCm1,ROCm0"); got[1] != "ROCm1" {
		t.Fatalf("first listed device: got %v", got)
	}
	if got := companion(t, "/app/bin/remote-llama cuda --model /m/x.gguf --spec-type draft-mtp --split-mode tensor"); got[1] != "CUDA0" {
		t.Fatalf("cuda runtime: got %v", got)
	}
	old := devicePresent
	defer func() { devicePresent = old }()
	devicePresent = func(p string) bool { return p == "/dev/nvidiactl" }
	if got := companion(t, "llama-server -m /m/x.gguf --spec-type draft-mtp -sm tensor"); got[1] != "CUDA0" {
		t.Fatalf("nvidia host: got %v", got)
	}
	devicePresent = func(string) bool { return false }
	if got := companion(t, "llama-server -m /m/x.gguf --spec-type draft-mtp -sm tensor"); got != nil {
		t.Fatalf("unknown host: no device guessed, got %v", got)
	}
}

func TestCompanionFlags_SanitizedCommandCarriesThem(t *testing.T) {
	m := ModelConfig{Cmd: gemmaTensorMTP}
	args, err := m.SanitizedCommand()
	if err != nil {
		t.Fatal(err)
	}
	tail := strings.Join(args[len(args)-2:], " ")
	if tail != "--device-draft ROCm0" {
		t.Fatalf("argv ends with %q", tail)
	}
	if got := m.CompanionFlags(); len(got) != 2 {
		t.Fatalf("CompanionFlags: %v", got)
	}
}
