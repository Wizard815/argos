package config

import "testing"

// store.path may name a database rather than a file. Without this it is stat-ed,
// found not to exist, and its "parent directory" checked for writability - which
// fails with an error about a directory that has nothing to do with anything.
func TestValidateStorePathAcceptsADatabaseURL(t *testing.T) {
	for _, url := range []string{
		"postgresql://argos:pw@postgres:5432/argos",
		"postgres://argos:pw@postgres:5432/argos",
		"  postgresql://argos:pw@postgres:5432/argos  ",
	} {
		if err := validateStorePath(url); err != nil {
			t.Errorf("validateStorePath(%q) = %v, want nil", url, err)
		}
	}
}

func TestValidateStorePathStillRejectsAnEmptyPath(t *testing.T) {
	if err := validateStorePath("   "); err == nil {
		t.Error("an empty store.path was accepted")
	}
}

func TestIsDatabaseURL(t *testing.T) {
	for _, yes := range []string{"postgres://h/d", "postgresql://h/d"} {
		if !isDatabaseURL(yes) {
			t.Errorf("isDatabaseURL(%q) = false", yes)
		}
	}
	for _, no := range []string{"", "/data/argos.db", ":memory:", "postgres.db", "./postgres"} {
		if isDatabaseURL(no) {
			t.Errorf("isDatabaseURL(%q) = true", no)
		}
	}
}
