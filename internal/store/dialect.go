package store

import (
	"strconv"
	"strings"
)

// The engine's store runs on either a SQLite file or Postgres, the same way the
// builder's does. Only two things about the SQL differ: how a statement spells
// its placeholders, and how it reports the id of a row it just inserted.
//
// Both are handled here so store.go stays ordinary SQL that reads the same on
// either database. Everything in this file is a pure function and is tested
// without a server.

// IsPostgres reports whether dsn names a Postgres database rather than a file.
func IsPostgres(dsn string) bool {
	d := strings.TrimSpace(dsn)
	return strings.HasPrefix(d, "postgres://") || strings.HasPrefix(d, "postgresql://")
}

// Rebind rewrites ? placeholders as $1, $2, ... which is what Postgres wants.
//
// A ? inside a string literal is data - a model id, an error message - and is
// left alone. The quote handling follows SQL's own rule that ” inside a
// literal is an escaped quote rather than the end of it.
func Rebind(query string) string {
	if !strings.ContainsRune(query, '?') {
		return query
	}
	var b strings.Builder
	b.Grow(len(query) + 8)
	n := 0
	inQuote := false
	for i := 0; i < len(query); i++ {
		c := query[i]
		if c == '\'' {
			if inQuote && i+1 < len(query) && query[i+1] == '\'' {
				b.WriteString("''")
				i++
				continue
			}
			inQuote = !inQuote
			b.WriteByte(c)
			continue
		}
		if c == '?' && !inQuote {
			n++
			b.WriteByte('$')
			b.WriteString(strconv.Itoa(n))
			continue
		}
		b.WriteByte(c)
	}
	return b.String()
}
