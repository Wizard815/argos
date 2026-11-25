package store

import (
	"context"
	"database/sql"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"time"
	"unicode/utf8"
)

// CaptureRecord is a persisted request/response capture. ID is the id of the
// activity row the capture belongs to. Headers are stored already redacted;
// the store does not redact anything itself.
//
// Bodies are text so the table reads cleanly in any SQL client: see BodyText.
// PromptText and ResponseText are the readable parts the server pulled out of
// the bodies (the last user message and the model's answer), for searching
// and for building training sets without parsing every API shape.
//
// The activity fields (Src, DurationMs, Tokens, ErrorMsg, Metadata) are read
// from the matching activity row when there is one; they are not stored twice.
type CaptureRecord struct {
	ID             int               `json:"id"`
	Timestamp      time.Time         `json:"timestamp"`
	Model          string            `json:"model"`
	ReqPath        string            `json:"req_path"`
	RespStatusCode int               `json:"resp_status_code"`
	PromptText     string            `json:"prompt_text"`
	ResponseText   string            `json:"response_text"`
	Src            string            `json:"src,omitempty"`
	DurationMs     int               `json:"duration_ms"`
	Tokens         TokenMetrics      `json:"tokens"`
	ErrorMsg       string            `json:"error_msg,omitempty"`
	Metadata       map[string]string `json:"metadata,omitempty"`
	ReqHeaders     map[string]string `json:"req_headers,omitempty"`
	ReqBody        string            `json:"req_body,omitempty"`
	RespHeaders    map[string]string `json:"resp_headers,omitempty"`
	RespBody       string            `json:"resp_body,omitempty"`
}

// CaptureQuery selects persisted captures. The zero value lists the oldest
// 100. Set fields are ANDed.
type CaptureQuery struct {
	AfterID  int  // id > AfterID
	BeforeID int  // id < BeforeID; 0 is unbounded
	Desc     bool // newest first instead of oldest first
	Limit    int  // 1..1000, default 100

	Model     string    // exact model id
	Path      string    // exact request path, e.g. /v1/chat/completions
	SrcPrefix string    // activity source prefix, e.g. "ip:10.0.0.5"
	Status    int       // exact response status code; 0 matches all
	Since     time.Time // inclusive; zero is unbounded
	Until     time.Time // inclusive; zero is unbounded
	Text      string    // case-insensitive substring of prompt, answer or request body

	// NoBodies leaves headers and raw bodies out of the result, keeping the
	// readable prompt_text and response_text. For browsing.
	NoBodies bool
}

const captureInsertColumns = `id, ts_created, model_id, req_path, resp_status_code,
	req_headers_json, req_body, resp_headers_json, resp_body, prompt_text, response_text`

// captureSelect reads a capture plus its activity row's fields. The activity
// row can be gone (pruned, or an in-memory store after a restart), so every
// activity column is COALESCEd.
const captureSelect = `SELECT c.id, c.ts_created, c.model_id, c.req_path, c.resp_status_code,
	c.prompt_text, c.response_text,
	COALESCE(a.src, ''), COALESCE(a.duration_ms, 0),
	COALESCE(a.cache_tokens, 0), COALESCE(a.input_tokens, 0), COALESCE(a.output_tokens, 0),
	COALESCE(a.prompt_per_second, 0), COALESCE(a.tokens_per_second, 0),
	COALESCE(a.error_msg, ''), COALESCE(a.metadata_json, ''),
	%s
	FROM captures c LEFT JOIN activity a ON a.id = c.id`

const captureBodyColumns = `c.req_headers_json, c.req_body, c.resp_headers_json, c.resp_body`
const captureNoBodyColumns = `'', '', '', ''`

// BodyText turns a raw body into the text stored in the captures table:
// UTF-8 as is, anything else as "base64:" followed by its base64.
func BodyText(b []byte) string {
	if utf8.Valid(b) {
		return string(b)
	}
	return "base64:" + base64.StdEncoding.EncodeToString(b)
}

// BodyBytes reverses BodyText.
func BodyBytes(s string) []byte {
	if rest, ok := strings.CutPrefix(s, "base64:"); ok {
		if b, err := base64.StdEncoding.DecodeString(rest); err == nil {
			return b
		}
	}
	return []byte(s)
}

// InsertCapture writes a capture. A second capture with the same id is
// ignored, so a retried write never fails on the primary key. Only the
// capture's own columns are written; the activity fields are ignored.
func (s *Store) InsertCapture(ctx context.Context, c CaptureRecord) error {
	if c.Timestamp.IsZero() {
		c.Timestamp = time.Now()
	}
	reqHeaders, err := marshalHeaders(c.ReqHeaders)
	if err != nil {
		return err
	}
	respHeaders, err := marshalHeaders(c.RespHeaders)
	if err != nil {
		return err
	}
	_, err = s.db.ExecContext(ctx, s.q(`INSERT INTO captures (`+captureInsertColumns+`)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
		ON CONFLICT (id) DO NOTHING`),
		c.ID, c.Timestamp.Unix(), c.Model, c.ReqPath, c.RespStatusCode,
		reqHeaders, c.ReqBody, respHeaders, c.RespBody, c.PromptText, c.ResponseText,
	)
	if err != nil {
		return fmt.Errorf("insert capture: %w", err)
	}
	return nil
}

// GetCapture returns the capture with id. found is false when there is none.
func (s *Store) GetCapture(ctx context.Context, id int) (rec CaptureRecord, found bool, err error) {
	query := fmt.Sprintf(captureSelect, captureBodyColumns) + ` WHERE c.id = ?`
	rec, err = scanCapture(s.db.QueryRowContext(ctx, s.q(query), id))
	if errors.Is(err, sql.ErrNoRows) {
		return CaptureRecord{}, false, nil
	}
	if err != nil {
		return CaptureRecord{}, false, err
	}
	return rec, true, nil
}

// ListCaptures returns the captures q selects. A collector pages through
// everything oldest first by passing the last id it saw as the next AfterID;
// a person browsing newest first passes the last id as BeforeID.
func (s *Store) ListCaptures(ctx context.Context, q CaptureQuery) ([]CaptureRecord, error) {
	if q.Limit <= 0 || q.Limit > 1000 {
		q.Limit = 100
	}
	cols := captureBodyColumns
	if q.NoBodies {
		cols = captureNoBodyColumns
	}
	where, args := s.captureWhere(q)
	order := "ASC"
	if q.Desc {
		order = "DESC"
	}
	query := fmt.Sprintf(captureSelect, cols) + where + ` ORDER BY c.id ` + order + ` LIMIT ?`
	args = append(args, q.Limit)

	rows, err := s.db.QueryContext(ctx, s.q(query), args...)
	if err != nil {
		return nil, fmt.Errorf("list captures: %w", err)
	}
	defer rows.Close()
	out := []CaptureRecord{}
	for rows.Next() {
		rec, err := scanCapture(rows)
		if err != nil {
			return nil, err
		}
		out = append(out, rec)
	}
	return out, rows.Err()
}

func (s *Store) captureWhere(q CaptureQuery) (string, []any) {
	conds := []string{"c.id > ?"}
	args := []any{q.AfterID}
	if q.BeforeID > 0 {
		conds = append(conds, "c.id < ?")
		args = append(args, q.BeforeID)
	}
	if q.Model != "" {
		conds = append(conds, "c.model_id = ?")
		args = append(args, q.Model)
	}
	if q.Path != "" {
		conds = append(conds, "c.req_path = ?")
		args = append(args, q.Path)
	}
	if q.SrcPrefix != "" {
		// Same literal-prefix range as activity: wildcards in it stay data.
		conds = append(conds, "a.src >= ? AND a.src < ?")
		args = append(args, q.SrcPrefix, q.SrcPrefix+"\U0010FFFF")
	}
	if q.Status != 0 {
		conds = append(conds, "c.resp_status_code = ?")
		args = append(args, q.Status)
	}
	if !q.Since.IsZero() {
		conds = append(conds, "c.ts_created >= ?")
		args = append(args, q.Since.Unix())
	}
	if !q.Until.IsZero() {
		conds = append(conds, "c.ts_created <= ?")
		args = append(args, q.Until.Unix())
	}
	if text := strings.TrimSpace(q.Text); text != "" {
		// SQLite's LIKE ignores ASCII case already; Postgres needs ILIKE.
		like := "LIKE"
		if s.postgres {
			like = "ILIKE"
		}
		pattern := "%" + escapeLike(text) + "%"
		conds = append(conds, fmt.Sprintf(
			`(c.prompt_text %[1]s ? ESCAPE '\' OR c.response_text %[1]s ? ESCAPE '\' OR c.req_body %[1]s ? ESCAPE '\')`, like))
		args = append(args, pattern, pattern, pattern)
	}
	return " WHERE " + strings.Join(conds, " AND "), args
}

// escapeLike makes s match literally inside a LIKE pattern with ESCAPE '\'.
func escapeLike(s string) string {
	return strings.NewReplacer(`\`, `\\`, `%`, `\%`, `_`, `\_`).Replace(s)
}

// CaptureIDs reports which of ids have a persisted capture.
func (s *Store) CaptureIDs(ctx context.Context, ids []int) (map[int]bool, error) {
	found := make(map[int]bool, len(ids))
	if len(ids) == 0 {
		return found, nil
	}
	placeholders := strings.TrimSuffix(strings.Repeat("?,", len(ids)), ",")
	args := make([]any, len(ids))
	for i, id := range ids {
		args[i] = id
	}
	rows, err := s.db.QueryContext(ctx, s.q(`SELECT id FROM captures WHERE id IN (`+placeholders+`)`), args...)
	if err != nil {
		return nil, fmt.Errorf("capture ids: %w", err)
	}
	defer rows.Close()
	for rows.Next() {
		var id int
		if err := rows.Scan(&id); err != nil {
			return nil, fmt.Errorf("scan capture id: %w", err)
		}
		found[id] = true
	}
	return found, rows.Err()
}

// PruneCaptures keeps the newest maxRows captures and deletes the rest.
// maxRows <= 0 keeps everything. Capture ids follow activity ids and have
// gaps, so the cut-off is the id of the maxRows-th newest row rather than
// arithmetic on MAX(id). With fewer rows than that the subquery is NULL and
// nothing is deleted.
func (s *Store) PruneCaptures(ctx context.Context, maxRows int) error {
	if maxRows <= 0 {
		return nil
	}
	if _, err := s.db.ExecContext(ctx, s.q(
		`DELETE FROM captures WHERE id < (SELECT id FROM captures ORDER BY id DESC LIMIT 1 OFFSET ?)`),
		maxRows-1,
	); err != nil {
		return fmt.Errorf("prune captures: %w", err)
	}
	return nil
}

func marshalHeaders(h map[string]string) (string, error) {
	if len(h) == 0 {
		return "", nil
	}
	data, err := json.Marshal(h)
	if err != nil {
		return "", fmt.Errorf("marshal capture headers: %w", err)
	}
	return string(data), nil
}

func scanCapture(scanner activityScanner) (CaptureRecord, error) {
	var rec CaptureRecord
	var ts int64
	var metadataJSON, reqHeaders, respHeaders string
	if err := scanner.Scan(
		&rec.ID, &ts, &rec.Model, &rec.ReqPath, &rec.RespStatusCode,
		&rec.PromptText, &rec.ResponseText,
		&rec.Src, &rec.DurationMs,
		&rec.Tokens.CachedTokens, &rec.Tokens.InputTokens, &rec.Tokens.OutputTokens,
		&rec.Tokens.PromptPerSecond, &rec.Tokens.TokensPerSecond,
		&rec.ErrorMsg, &metadataJSON,
		&reqHeaders, &rec.ReqBody, &respHeaders, &rec.RespBody,
	); err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			return CaptureRecord{}, err
		}
		return CaptureRecord{}, fmt.Errorf("scan capture: %w", err)
	}
	rec.Timestamp = time.Unix(ts, 0)
	for _, f := range []struct {
		raw  string
		dest *map[string]string
		what string
	}{
		{metadataJSON, &rec.Metadata, "metadata"},
		{reqHeaders, &rec.ReqHeaders, "request headers"},
		{respHeaders, &rec.RespHeaders, "response headers"},
	} {
		if f.raw == "" {
			continue
		}
		if err := json.Unmarshal([]byte(f.raw), f.dest); err != nil {
			return CaptureRecord{}, fmt.Errorf("unmarshal capture %s: %w", f.what, err)
		}
	}
	return rec, nil
}
