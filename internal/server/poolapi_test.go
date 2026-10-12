package server

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/mostlygeek/llama-swap/internal/router"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

// reportingRouter is a stubRouter that also describes a pool.
type reportingRouter struct {
	*stubRouter
	status router.PoolStatus
}

func (r *reportingRouter) PoolStatus() router.PoolStatus { return r.status }

func TestServer_APIPoolReportsPoolStatus(t *testing.T) {
	local := &reportingRouter{stubRouter: &stubRouter{}, status: router.PoolStatus{
		Router: "pool", Enabled: true, Placement: true, BudgetGiB: 62,
		Cards:  []router.PoolCardStatus{{ID: "0", CapGiB: 31, UsedGiB: 26, FreeGiB: 5, Models: []string{"a"}}},
		Models: []router.PoolModelStatus{{ID: "a", State: "ready", SizeGiB: 26, SizeKnown: true, Cards: []string{"0"}}},
	}}
	s := newTestServer(local, &stubRouter{})

	w := httptest.NewRecorder()
	s.ServeHTTP(w, httptest.NewRequest(http.MethodGet, "/api/pool", nil))

	require.Equal(t, http.StatusOK, w.Code)
	var got map[string]any
	require.NoError(t, json.Unmarshal(w.Body.Bytes(), &got))
	assert.Equal(t, true, got["enabled"])
	assert.Equal(t, "pool", got["router"])
	cards := got["cards"].([]any)
	require.Len(t, cards, 1)
	assert.Equal(t, 26.0, cards[0].(map[string]any)["used_gib"])
}

func TestServer_APIPoolWithoutPool(t *testing.T) {
	s := newTestServer(&stubRouter{}, &stubRouter{})

	w := httptest.NewRecorder()
	s.ServeHTTP(w, httptest.NewRequest(http.MethodGet, "/api/pool", nil))

	require.Equal(t, http.StatusOK, w.Code)
	var got map[string]any
	require.NoError(t, json.Unmarshal(w.Body.Bytes(), &got))
	assert.Equal(t, false, got["enabled"])
}
