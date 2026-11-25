package store

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"time"
)

// CaptureRecord is a persisted request/response capture. ID is the id of the
// activity row the capture belongs to. Headers are stored already redacted;
// the store does not redact anything itself.
type CaptureRecord struct {
	ID             int               `json:"id"`
	Timestamp      time.Time         `json:"timestamp"`
	Model          string            `json:"model"`
	ReqPath        string            `json:"req_path"`
	RespStatusCode int               `json:"resp_status_code"`
	ReqHeaders     map[string]string `json:"req_headers"`
	ReqBody        []byte            `json:"req_body"`
	RespHeaders    map[string]string `json:"resp_headers"`
	RespBody       []byte            `json:"resp_body"`
}

const captureColumns = `id, ts_created, model_id, req_path, resp_status_code,
	req_headers_json, req_body, resp_headers_json, resp_body`

// InsertCapture writes a capture. A second capture with the same id is
// ignored, so a retried write never fails on the primary key.
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
	_, err = s.db.ExecContext(ctx, s.q(`INSERT INTO captures (`+captureColumns+`)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
		ON CONFLICT (id) DO NOTHING`),
		c.ID, c.Timestamp.Unix(), c.Model, c.ReqPath, c.RespStatusCode,
		reqHeaders, c.ReqBody, respHeaders, c.RespBody,
	)
	if err != nil {
		return fmt.Errorf("insert capture: %w", err)
	}
	return nil
}

// GetCapture returns the capture with id. found is false when there is none.
func (s *Store) GetCapture(ctx context.Context, id int) (rec CaptureRecord, found bool, err error) {
	row := s.db.QueryRowContext(ctx, s.q(`SELECT `+captureColumns+` FROM captures WHERE id = ?`), id)
	rec, err = scanCapture(row)
	if errors.Is(err, sql.ErrNoRows) {
		return CaptureRecord{}, false, nil
	}
	if err != nil {
		return CaptureRecord{}, false, err
	}
	return rec, true, nil
}

// ListCaptures returns up to limit captures with an id greater than afterID,
// oldest first. A collector pages through everything by passing the last id
// it saw as the next afterID.
func (s *Store) ListCaptures(ctx context.Context, afterID, limit int) ([]CaptureRecord, error) {
	if limit <= 0 || limit > 1000 {
		limit = 100
	}
	rows, err := s.db.QueryContext(ctx,
		s.q(`SELECT `+captureColumns+` FROM captures WHERE id > ? ORDER BY id ASC LIMIT ?`),
		afterID, limit,
	)
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
	var reqHeaders, respHeaders string
	if err := scanner.Scan(
		&rec.ID, &ts, &rec.Model, &rec.ReqPath, &rec.RespStatusCode,
		&reqHeaders, &rec.ReqBody, &respHeaders, &rec.RespBody,
	); err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			return CaptureRecord{}, err
		}
		return CaptureRecord{}, fmt.Errorf("scan capture: %w", err)
	}
	rec.Timestamp = time.Unix(ts, 0)
	if reqHeaders != "" {
		if err := json.Unmarshal([]byte(reqHeaders), &rec.ReqHeaders); err != nil {
			return CaptureRecord{}, fmt.Errorf("unmarshal capture request headers: %w", err)
		}
	}
	if respHeaders != "" {
		if err := json.Unmarshal([]byte(respHeaders), &rec.RespHeaders); err != nil {
			return CaptureRecord{}, fmt.Errorf("unmarshal capture response headers: %w", err)
		}
	}
	return rec, nil
}
