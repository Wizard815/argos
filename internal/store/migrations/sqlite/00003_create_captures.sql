-- +goose Up
-- One row per persisted request/response capture. id is the activity row's
-- id, so a collector can join the two tables. Sensitive headers are redacted
-- before a row is written.
CREATE TABLE captures (
    id INTEGER PRIMARY KEY,
    ts_created INTEGER NOT NULL,
    model_id TEXT NOT NULL DEFAULT '',
    req_path TEXT NOT NULL DEFAULT '',
    resp_status_code INTEGER NOT NULL DEFAULT 0,
    req_headers_json TEXT NOT NULL DEFAULT '',
    req_body BLOB,
    resp_headers_json TEXT NOT NULL DEFAULT '',
    resp_body BLOB
);

CREATE INDEX idx_captures_created_id
    ON captures (ts_created DESC, id DESC);

-- +goose Down
DROP TABLE captures;
