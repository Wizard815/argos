package server

import (
	"context"
	"encoding/json"
	"fmt"
	"time"

	"github.com/Wizard815/Argos/internal/mcptools"
	"github.com/Wizard815/Argos/internal/router"
	"github.com/Wizard815/Argos/internal/store"
)

// scheduleProvider serves what the GPUs are doing over MCP: the pool's cards
// and placement, which models are loaded, reading a prompt (prefill) or
// answering, what is queued or loading, the requests still running and the
// ones that just finished. The same facts as /api/pool, /api/inflight and
// /api/metrics/activity, shaped for a model or a dashboard to read. All of it
// is read-only.
type scheduleProvider struct {
	s *Server
}

var _ mcptools.Provider = (*scheduleProvider)(nil)

func (p *scheduleProvider) ID() string { return "schedule" }

func (p *scheduleProvider) Shutdown(time.Duration) error { return nil }

func noArgs() map[string]any {
	return map[string]any{"type": "object", "properties": map[string]any{}, "additionalProperties": false}
}

func (p *scheduleProvider) Tools(context.Context) ([]mcptools.Tool, error) {
	live := &mcptools.Annotations{ReadOnlyHint: true, IdempotentHint: false}
	return []mcptools.Tool{
		{
			Name:  "pool_status",
			Title: "What the GPUs are doing",
			Description: "The scheduler's view right now: each GPU card with its planned use and free GiB, every " +
				"model that is not stopped with its state, cards, size and requests in flight, and for a model " +
				"reading a long prompt its prefill progress (tokens done of total, tokens per second). Also " +
				"the queue (requests waiting for room or a busy model) and models loading. Only the pool " +
				"router schedules; with another router this says which one is in use.",
			Annotations: live,
			Tags:        []string{"schedule", "gpu", "prefill", "queue", "pool"},
			InputSchema: noArgs(),
		},
		{
			Name:  "requests_in_flight",
			Title: "Requests running now",
			Description: "Every model request still running, oldest first: the model, the endpoint, who sent it, " +
				"how long it has run and how many bytes of answer it has sent (0 while the prompt is still " +
				"being read).",
			Annotations: live,
			Tags:        []string{"requests", "inflight", "running"},
			InputSchema: noArgs(),
		},
		{
			Name:  "recent_requests",
			Title: "Requests that just finished",
			Description: "The newest finished requests, newest first, with tokens in and out, prompt and " +
				"generation speed, duration and status. limit is how many (default 20, at most 100).",
			Annotations: live,
			Tags:        []string{"requests", "activity", "history", "speed"},
			InputSchema: map[string]any{
				"type": "object",
				"properties": map[string]any{
					"limit": map[string]any{"type": "integer", "minimum": 1, "maximum": 100},
				},
				"additionalProperties": false,
			},
		},
	}, nil
}

func (p *scheduleProvider) Call(ctx context.Context, name string, args map[string]json.RawMessage) (mcptools.Result, error) {
	switch name {
	case "pool_status":
		return p.poolStatus(), nil
	case "requests_in_flight":
		return jsonResult(map[string]any{"requests": p.s.inflight.Current().Requests}), nil
	case "recent_requests":
		return p.recent(ctx, args), nil
	default:
		return mcptools.Result{}, fmt.Errorf("unknown tool %q", name)
	}
}

// poolStatus leaves out stopped models: a config can list dozens, and what is
// stopped is not what anyone asking "what is running" wants to read through.
func (p *scheduleProvider) poolStatus() mcptools.Result {
	reporter, ok := p.s.local.(router.PoolReporter)
	if !ok {
		return jsonResult(map[string]any{"router": p.s.cfg.Routing.Router.Use, "enabled": false,
			"note": "this router does not schedule; switch routing to the pool router to see placement"})
	}
	st := reporter.PoolStatus()
	active := st.Models[:0:0]
	stopped := 0
	for _, m := range st.Models {
		if m.State == "stopped" {
			stopped++
			continue
		}
		active = append(active, m)
	}
	st.Models = active
	return jsonResult(map[string]any{"pool": st, "stopped_models": stopped})
}

func (p *scheduleProvider) recent(ctx context.Context, args map[string]json.RawMessage) mcptools.Result {
	limit := 20
	if raw, ok := args["limit"]; ok {
		if err := json.Unmarshal(raw, &limit); err != nil || limit < 1 || limit > 100 {
			return mcptools.Result{IsError: true, Content: "Error: limit must be a whole number from 1 to 100."}
		}
	}
	page, err := p.s.store.ListActivity(ctx, store.ActivityQuery{Limit: limit, Page: 1, Order: "desc"})
	if err != nil {
		return mcptools.Result{IsError: true, Content: fmt.Sprintf("Error: reading the activity log: %v", err)}
	}
	type row struct {
		Time       time.Time `json:"time"`
		Model      string    `json:"model"`
		Path       string    `json:"path"`
		From       string    `json:"from"`
		Status     int       `json:"status"`
		In         int       `json:"input_tokens"`
		Out        int       `json:"output_tokens"`
		Cached     int       `json:"cache_tokens"`
		PromptTPS  float64   `json:"prompt_tokens_per_second"`
		GenTPS     float64   `json:"tokens_per_second"`
		DurationMs int       `json:"duration_ms"`
		Error      string    `json:"error,omitempty"`
	}
	rows := make([]row, 0, len(page.Data))
	for _, e := range page.Data {
		rows = append(rows, row{e.Timestamp, e.Model, e.ReqPath, e.Src, e.RespStatusCode, e.Tokens.InputTokens,
			e.Tokens.OutputTokens, e.Tokens.CachedTokens, e.Tokens.PromptPerSecond, e.Tokens.TokensPerSecond,
			e.DurationMs, e.ErrorMsg})
	}
	return jsonResult(map[string]any{"requests": rows})
}

func jsonResult(v any) mcptools.Result {
	b, err := json.MarshalIndent(v, "", " ")
	if err != nil {
		return mcptools.Result{IsError: true, Content: fmt.Sprintf("Error: %v", err)}
	}
	return mcptools.CapResult(string(b), mcptools.MaxToolResultBytes)
}
