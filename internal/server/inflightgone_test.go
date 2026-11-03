package server

import (
	"context"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/Wizard815/Argos/internal/config"
	"github.com/Wizard815/Argos/internal/swaputil"
)

// A client that hangs up mid-request is noted as gone (with its model), so a
// slot still reading its prompt can say "client gone"; one that finishes is not.
func TestInflight_AClientThatHangsUpIsNotedAsGone(t *testing.T) {
	tr := newInflightTracker()
	mw := CreateInflightMiddleware(tr, config.Config{})
	started := make(chan struct{})
	slow := mw(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		close(started)
		<-r.Context().Done() // the model keeps "working" until the client is gone
	}))
	ctx, hangUp := context.WithCancel(context.Background())
	ctx = swaputil.SetContext(ctx, swaputil.ReqContextData{ModelID: "coder", Metadata: map[string]string{}})
	req := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", nil).WithContext(ctx)
	done := make(chan struct{})
	go func() { slow.ServeHTTP(httptest.NewRecorder(), req); close(done) }()
	<-started
	hangUp()
	<-done
	gone := tr.Gone(time.Minute)
	if len(gone) != 1 || gone[0].Model != "coder" {
		t.Fatalf("want one gone request for coder, got %+v", gone)
	}

	ok := mw(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { w.WriteHeader(200) }))
	ctx2 := swaputil.SetContext(context.Background(), swaputil.ReqContextData{ModelID: "coder", Metadata: map[string]string{}})
	ok.ServeHTTP(httptest.NewRecorder(), httptest.NewRequest(http.MethodPost, "/v1/chat/completions", nil).WithContext(ctx2))
	if n := len(tr.Gone(time.Minute)); n != 1 {
		t.Fatalf("a request that finished is not gone: %d", n)
	}
}
