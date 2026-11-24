package server

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"sort"
	"strings"
	"testing"

	"github.com/Wizard815/Argos/internal/config"
	"github.com/Wizard815/Argos/internal/modelscan"
)

const longBase = "qwen3.8-27b-turbofcfusion-735-882-here-uncen-neo-coder-max-mtp-q8_0"

func publicListServer(t *testing.T) (*Server, *string) {
	t.Helper()
	base := map[string]any{modelscan.BaseModelKey: true}
	cfg := config.Config{
		ModelTogglesFile: filepath.Join(t.TempDir(), "toggles.json"),
		Models: map[string]config.ModelConfig{
			longBase: {Cmd: "llama-server -m a.gguf", Metadata: base},
			longBase + "--qwen3.8-27b-uncen-mtp-2chat-512k": {Cmd: "llama-server -m a.gguf -c 524288"},
			"gemma-3-12b":       {Cmd: "llama-server -m g.gguf", Metadata: base},
			"gemma-3-12b--fast": {Cmd: "llama-server -m g.gguf -c 8192"},
			"glm-4.6":           {Cmd: "llama-server -m glm.gguf", Metadata: base},
			"glm-4.6--fast":     {Cmd: "llama-server -m glm.gguf -c 8192"},
			"openjev":           {Cmd: "llama-server -m openjev.gguf", Metadata: base},
			"whisper":           {Cmd: "whisper-server"}, // written by hand: always listed
		},
	}
	var got string
	r := newStubRouter(nil, "")
	for id := range cfg.Models {
		r.models[id] = true
	}
	r.serveHTTP = func(w http.ResponseWriter, req *http.Request) {
		var body struct {
			Model string `json:"model"`
		}
		_ = json.NewDecoder(req.Body).Decode(&body)
		got = body.Model
		w.Write([]byte("ok"))
	}
	return newTestServerWithConfig(cfg, r), &got
}

func listIDs(t *testing.T, s *Server, outside bool, query string) []string {
	t.Helper()
	req := httptest.NewRequest(http.MethodGet, "/v1/models"+query, nil)
	if outside {
		req.Header.Set("X-Forwarded-For", "192.168.20.7")
	}
	w := httptest.NewRecorder()
	s.ServeHTTP(w, req)
	var out struct {
		Data []struct {
			ID string `json:"id"`
		} `json:"data"`
	}
	if err := json.Unmarshal(w.Body.Bytes(), &out); err != nil {
		t.Fatalf("list: %v %s", err, w.Body.String())
	}
	ids := []string{}
	for _, d := range out.Data {
		ids = append(ids, d.ID)
	}
	sort.Strings(ids)
	return ids
}

func call(s *Server, model string, outside bool) *httptest.ResponseRecorder {
	req := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", strings.NewReader(`{"model":"`+model+`"}`))
	req.Header.Set("Content-Type", "application/json")
	if outside {
		req.Header.Set("X-Forwarded-For", "192.168.20.7")
	}
	w := httptest.NewRecorder()
	s.ServeHTTP(w, req)
	return w
}

func TestPublicList_OutsideSeesProfilesByLabelOnly(t *testing.T) {
	s, _ := publicListServer(t)
	got := strings.Join(listIDs(t, s, true, ""), " ")
	want := "gemma-3-12b--fast glm-4.6--fast qwen3.8-27b-uncen-mtp-2chat-512k whisper"
	if got != want {
		t.Fatalf("outside list = %s\nwant          %s", got, want)
	}
	if all := listIDs(t, s, false, ""); len(all) != 8 {
		t.Fatalf("inside list = %v, want all 8", all)
	}
	if all := listIDs(t, s, true, "?all=1"); len(all) != 8 {
		t.Fatalf("?all=1 = %v, want all 8", all)
	}
}

func TestPublicList_LabelRunsTheProfile(t *testing.T) {
	s, got := publicListServer(t)
	if w := call(s, "qwen3.8-27b-uncen-mtp-2chat-512k", true); w.Code != http.StatusOK {
		t.Fatalf("status=%d %s", w.Code, w.Body.String())
	}
	if *got != longBase+"--qwen3.8-27b-uncen-mtp-2chat-512k" {
		t.Fatalf("ran %q", *got)
	}
	if w := call(s, longBase+"--qwen3.8-27b-uncen-mtp-2chat-512k", true); w.Code != http.StatusOK {
		t.Fatalf("the full id still works: status=%d", w.Code)
	}
}

func TestPublicList_BaseModelNeedsAProfileFromOutside(t *testing.T) {
	s, _ := publicListServer(t)
	w := call(s, "openjev", true)
	if w.Code != http.StatusNotFound || !strings.Contains(w.Body.String(), "make a profile") {
		t.Fatalf("outside call to a base model: status=%d %s", w.Code, w.Body.String())
	}
	if w := call(s, "openjev", false); w.Code != http.StatusOK {
		t.Fatalf("inside call: status=%d", w.Code)
	}
}

func TestPublicList_ShowBaseModelByHand(t *testing.T) {
	s, _ := publicListServer(t)
	req := httptest.NewRequest(http.MethodPut, "/api/models/openjev/listed", strings.NewReader(`{"listed":true}`))
	w := httptest.NewRecorder()
	s.ServeHTTP(w, req)
	if w.Code != http.StatusOK {
		t.Fatalf("PUT listed: %d %s", w.Code, w.Body.String())
	}
	if ids := strings.Join(listIDs(t, s, true, ""), " "); !strings.Contains(ids, "openjev") {
		t.Fatalf("listed base model missing: %s", ids)
	}
	if w := call(s, "openjev", true); w.Code != http.StatusOK {
		t.Fatalf("listed base model call: %d", w.Code)
	}
	// it survives a restart
	tf := readTogglesFile(s.cfg.ModelTogglesFile)
	if len(tf.Listed) != 1 || tf.Listed[0] != "openjev" {
		t.Fatalf("saved %v", tf.Listed)
	}
	if err := saveModelToggles(s.cfg.ModelTogglesFile, map[string]string{"glm-4.6": ModeSingleGPU}); err != nil {
		t.Fatal(err)
	}
	if tf := readTogglesFile(s.cfg.ModelTogglesFile); len(tf.Listed) != 1 || tf.Modes["glm-4.6"] != ModeSingleGPU {
		t.Fatalf("saving modes lost the listed models: %+v", tf)
	}
}

func putPublicList(t *testing.T, s *Server, body string) {
	t.Helper()
	req := httptest.NewRequest(http.MethodPut, "/api/models/public-list", strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	s.ServeHTTP(w, req)
	if w.Code != http.StatusOK {
		t.Fatalf("PUT public-list: %d %s", w.Code, w.Body.String())
	}
}

func TestPublicList_FullNamesShowTheModelAndTheProfile(t *testing.T) {
	s, got := publicListServer(t)
	putPublicList(t, s, `{"names":"full"}`)
	ids := strings.Join(listIDs(t, s, true, ""), " ")
	want := "gemma-3-12b--fast glm-4.6--fast " + longBase + "--qwen3.8-27b-uncen-mtp-2chat-512k whisper"
	if ids != want {
		t.Fatalf("outside list = %s\nwant          %s", ids, want)
	}
	if w := call(s, "qwen3.8-27b-uncen-mtp-2chat-512k", true); w.Code != http.StatusOK || *got != longBase+"--qwen3.8-27b-uncen-mtp-2chat-512k" {
		t.Fatalf("the label still runs the profile: %d %q", w.Code, *got)
	}
}

func TestPublicList_ModelsWithoutAProfileCanBeShown(t *testing.T) {
	s, _ := publicListServer(t)
	putPublicList(t, s, `{"bases":"unprofiled"}`)
	ids := strings.Join(listIDs(t, s, true, ""), " ")
	if !strings.Contains(ids, "openjev") || strings.Contains(ids, "glm-4.6 ") || strings.Contains(ids, "gemma-3-12b ") {
		t.Fatalf("only the base model with no profile is added: %s", ids)
	}
	if w := call(s, "openjev", true); w.Code != http.StatusOK {
		t.Fatalf("a shown base model can be called: %d", w.Code)
	}
	putPublicList(t, s, `{"bases":"all"}`)
	if n := len(listIDs(t, s, true, "")); n != 8 {
		t.Fatalf("all: %d models", n)
	}
	// saved, and kept when the GPU modes are saved
	if err := saveModelToggles(s.cfg.ModelTogglesFile, map[string]string{}); err != nil {
		t.Fatal(err)
	}
	if tf := readTogglesFile(s.cfg.ModelTogglesFile); tf.PublicList == nil || tf.PublicList.Bases != "all" {
		t.Fatalf("saved %+v", tf.PublicList)
	}
}
