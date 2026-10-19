package store

import "testing"

func TestIsPostgres(t *testing.T) {
	yes := []string{"postgres://u:p@h:5432/db", "postgresql://u:p@h/db", "  postgresql://h/db  "}
	for _, dsn := range yes {
		if !IsPostgres(dsn) {
			t.Errorf("IsPostgres(%q) = false, want true", dsn)
		}
	}
	no := []string{"", "/data/argos.db", ":memory:", "sqlite://x", "postgres", "./postgres.db"}
	for _, dsn := range no {
		if IsPostgres(dsn) {
			t.Errorf("IsPostgres(%q) = true, want false", dsn)
		}
	}
}

func TestRebind(t *testing.T) {
	got := Rebind(`INSERT INTO activity (a, b, c) VALUES (?, ?, ?)`)
	want := `INSERT INTO activity (a, b, c) VALUES ($1, $2, $3)`
	if got != want {
		t.Fatalf("Rebind = %q, want %q", got, want)
	}
}

func TestRebindNumbersFromOneInOrder(t *testing.T) {
	got := Rebind(`SELECT * FROM activity WHERE model_id = ? AND ts_created > ? LIMIT ? OFFSET ?`)
	want := `SELECT * FROM activity WHERE model_id = $1 AND ts_created > $2 LIMIT $3 OFFSET $4`
	if got != want {
		t.Fatalf("Rebind = %q, want %q", got, want)
	}
}

// A question mark in a model id or an error message is data, not a placeholder.
func TestRebindLeavesQuestionMarksInLiteralsAlone(t *testing.T) {
	got := Rebind(`SELECT * FROM activity WHERE model_id = 'why?' AND src = ?`)
	want := `SELECT * FROM activity WHERE model_id = 'why?' AND src = $1`
	if got != want {
		t.Fatalf("Rebind = %q, want %q", got, want)
	}
}

// ” inside a literal is an escaped quote, not the end of the literal.
func TestRebindHandlesAnEscapedQuote(t *testing.T) {
	got := Rebind(`SELECT * FROM activity WHERE error_msg = 'it''s ok?' AND src = ?`)
	want := `SELECT * FROM activity WHERE error_msg = 'it''s ok?' AND src = $1`
	if got != want {
		t.Fatalf("Rebind = %q, want %q", got, want)
	}
}

func TestRebindLeavesAQueryWithoutPlaceholdersAlone(t *testing.T) {
	got := Rebind(`SELECT COUNT(*) FROM activity`)
	if got != `SELECT COUNT(*) FROM activity` {
		t.Fatalf("Rebind = %q", got)
	}
}

func TestRebindKeepsTheCountInStepWithTheArgs(t *testing.T) {
	// A statement that has already been rebound must not be rebound again into
	// different numbers, or the arguments would land in the wrong columns.
	once := Rebind(`SELECT * FROM activity WHERE a = ? AND b = ?`)
	if again := Rebind(once); again != once {
		t.Fatalf("Rebind is not stable: %q then %q", once, again)
	}
}
