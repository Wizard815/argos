package store

import (
	"context"
	"os"
	"testing"
	"time"
)

// These run against a real Postgres. Point ARGOS_TEST_POSTGRES at one to turn
// them on; without it they are skipped, so the ordinary `go test ./...` needs no
// server and stays fast.
//
//	ARGOS_TEST_POSTGRES=postgresql://u:p@127.0.0.1:5432/argos_test go test ./internal/store/
func postgresDSN(t *testing.T) string {
	t.Helper()
	dsn := os.Getenv("ARGOS_TEST_POSTGRES")
	if dsn == "" {
		t.Skip("set ARGOS_TEST_POSTGRES to run the Postgres tests")
	}
	return dsn
}

func TestPostgresOpensAndMigrates(t *testing.T) {
	dsn := postgresDSN(t)
	st, err := New(dsn)
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	defer st.Close()

	ctx := context.Background()
	if _, err := st.db.ExecContext(ctx, `DELETE FROM activity`); err != nil {
		t.Fatalf("clear activity: %v", err)
	}
}

func TestPostgresRoundTripsAnEntry(t *testing.T) {
	dsn := postgresDSN(t)
	st, err := New(dsn)
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	defer st.Close()
	ctx := context.Background()
	if _, err := st.db.ExecContext(ctx, `DELETE FROM activity`); err != nil {
		t.Fatalf("clear activity: %v", err)
	}

	// The id is the part Postgres reports differently: there is no LastInsertId,
	// so this is what proves RETURNING works.
	entry, err := st.InsertActivity(ctx, ActivityLogEntry{
		Model:   "qwen3-0.6b-q8_0",
		Src:     "argos",
		ReqPath: "/v1/chat/completions",
		Tokens:  TokenMetrics{InputTokens: 12, OutputTokens: 34, TokensPerSecond: 80.5},
	})
	if err != nil {
		t.Fatalf("InsertActivity: %v", err)
	}
	if entry.ID <= 0 {
		t.Fatalf("InsertActivity returned id %d, want a positive one", entry.ID)
	}

	second, err := st.InsertActivity(ctx, ActivityLogEntry{Model: "other", Tokens: TokenMetrics{TokensPerSecond: 40}})
	if err != nil {
		t.Fatalf("second InsertActivity: %v", err)
	}
	if second.ID <= entry.ID {
		t.Fatalf("second id %d is not above the first %d", second.ID, entry.ID)
	}

	page, err := st.ListActivity(ctx, ActivityQuery{Limit: 10})
	if err != nil {
		t.Fatalf("ListActivity: %v", err)
	}
	if page.Total != 2 || len(page.Data) != 2 {
		t.Fatalf("ListActivity returned %d of %d, want 2 of 2", len(page.Data), page.Total)
	}

	stats, err := st.ActivityStats(ctx, ActivityStatsQuery{})
	if err != nil {
		t.Fatalf("ActivityStats: %v", err)
	}
	if stats.TotalRequests != 2 || stats.TotalInputTokens != 12 || stats.TotalOutputTokens != 34 {
		t.Fatalf("stats = %+v", stats)
	}
}

func TestPostgresPrunes(t *testing.T) {
	dsn := postgresDSN(t)
	st, err := New(dsn)
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	defer st.Close()
	ctx := context.Background()
	if _, err := st.db.ExecContext(ctx, `DELETE FROM activity`); err != nil {
		t.Fatalf("clear activity: %v", err)
	}

	for i := 0; i < 5; i++ {
		if _, err := st.InsertActivity(ctx, ActivityLogEntry{
			Model:     "m",
			Timestamp: time.Unix(int64(1000+i), 0),
		}); err != nil {
			t.Fatalf("insert %d: %v", i, err)
		}
	}
	if err := st.PruneActivity(ctx, 2); err != nil {
		t.Fatalf("PruneActivity: %v", err)
	}
	page, err := st.ListActivity(ctx, ActivityQuery{Limit: 10})
	if err != nil {
		t.Fatalf("ListActivity: %v", err)
	}
	if page.Total != 2 {
		t.Fatalf("after pruning to 2, %d rows remain", page.Total)
	}
}
