package store

import (
	"context"
	"testing"
	"time"
)

// An activity row carries the id that actually served, which for a pinned model
// is its profile variant (<base>--<label>). Filtering on the base model has to
// include those rows, or the model's own activity page reads as empty.
func TestStore_ActivityFilterIncludesProfileVariants(t *testing.T) {
	ctx := context.Background()
	st, err := New("")
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	defer st.Close()

	// ornithish shares the prefix but is a different model, not a variant.
	for i, model := range []string{"ornith", "ornith--fast", "ornith--fast-2", "other", "ornithish"} {
		if _, err := st.InsertActivity(ctx, ActivityLogEntry{
			Timestamp: time.Unix(int64(100+i), 0),
			Model:     model,
			ReqPath:   "/v1/chat/completions",
		}); err != nil {
			t.Fatalf("InsertActivity: %v", err)
		}
	}

	page, err := st.ListActivity(ctx, ActivityQuery{
		ActivityFilter: ActivityFilter{Models: []string{"ornith"}},
		Limit:          10,
		Page:           1,
	})
	if err != nil {
		t.Fatalf("ListActivity: %v", err)
	}

	got := map[string]bool{}
	for _, e := range page.Data {
		got[e.Model] = true
	}
	if page.Total != 3 {
		t.Errorf("total = %d, want 3 (the model and its two variants): %v", page.Total, got)
	}
	if !got["ornith"] || !got["ornith--fast"] || !got["ornith--fast-2"] {
		t.Errorf("missing the model or one of its variants: %v", got)
	}
	if got["other"] || got["ornithish"] {
		t.Errorf("matched a model outside the family: %v", got)
	}
}
