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

func TestCompanionFlags_TensorAndMTPDraftGetsDraftOnOneCardWithF16Cache(t *testing.T) {
	want := []string{"--device-draft", "ROCm0", "--cache-type-k-draft", "f16", "--cache-type-v-draft", "f16"}
	if got := companion(t, gemmaTensorMTP); !reflect.DeepEqual(got, want) {
		t.Fatalf("got %v, want %v", got, want)
	}
}

func TestCompanionFlags_BuiltInMTPLayersAndEqualsSpellingCount(t *testing.T) {
	got := companion(t, "/app/bin/remote-llama rocm --model /m/Qwen3.8-27B-MTP-Q8_0.gguf --spec-type=draft-mtp -sm=tensor -ctk q4_0 -ctv q4_0")
	if len(got) != 6 || got[1] != "ROCm0" {
		t.Fatalf("got %v", got)
	}
}

func TestCompanionFlags_WhatTheCommandSetsWins(t *testing.T) {
	if got := companion(t, gemmaTensorMTP+" -devd ROCm1 -ctkd q8_0"); got != nil {
		t.Fatalf("set by hand, nothing to add: got %v", got)
	}
	if got := companion(t, gemmaTensorMTP+" --device-draft=ROCm1"); !reflect.DeepEqual(got, []string{"--cache-type-k-draft", "f16", "--cache-type-v-draft", "f16"}) {
		t.Fatalf("got %v", got)
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
	if got := companion(t, strings.ReplaceAll(gemmaTensorMTP, "q8_0", "f16")); !reflect.DeepEqual(got, []string{"--device-draft", "ROCm0"}) {
		t.Fatalf("unquantised cache needs no draft cache flag: got %v", got)
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
	tail := strings.Join(args[len(args)-6:], " ")
	if tail != "--device-draft ROCm0 --cache-type-k-draft f16 --cache-type-v-draft f16" {
		t.Fatalf("argv ends with %q", tail)
	}
	if got := m.CompanionFlags(); len(got) != 6 {
		t.Fatalf("CompanionFlags: %v", got)
	}
}
