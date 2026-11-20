package server

import (
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

// TestServer_ContinueKeepsTheRealStatus: an upstream that answers "Expect:
// 100-continue" with 100 and then fails must reach the client as its failure,
// not as 200 (llama-swap #1319).
func TestServer_ContinueKeepsTheRealStatus(t *testing.T) {
	r := newStubRouter([]string{"m"}, "")
	r.serveHTTP = func(w http.ResponseWriter, req *http.Request) {
		w.WriteHeader(http.StatusContinue)
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusBadRequest)
		io.WriteString(w, `{"error":"context too long"}`)
	}
	srv := httptest.NewServer(newTestServer(r))
	defer srv.Close()

	body := `{"model":"m","messages":[{"role":"user","content":"` + strings.Repeat("x", 2<<20) + `"}]}`
	req, _ := http.NewRequest(http.MethodPost, srv.URL+"/v1/chat/completions", strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Expect", "100-continue")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	got, _ := io.ReadAll(resp.Body)
	if resp.StatusCode != http.StatusBadRequest || !strings.Contains(string(got), "context too long") {
		t.Fatalf("status=%d body=%s, want the upstream's 400", resp.StatusCode, got)
	}
}
