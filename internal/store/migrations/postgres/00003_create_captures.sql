-- +goose Up
-- One row per persisted request/response capture. id is the activity row's
-- id, so a collector can join the two tables. Sensitive headers are redacted
-- before a row is written.
CREATE TABLE IF NOT EXISTS captures (
    id BIGINT PRIMARY KEY,
    ts_created BIGINT NOT NULL,
    model_id TEXT NOT NULL DEFAULT '',
    req_path TEXT NOT NULL DEFAULT '',
    resp_status_code INTEGER NOT NULL DEFAULT 0,
    req_headers_json TEXT NOT NULL DEFAULT '',
    req_body BYTEA,
    resp_headers_json TEXT NOT NULL DEFAULT '',
    resp_body BYTEA
);

CREATE INDEX IF NOT EXISTS idx_captures_created_id
    ON captures (ts_created DESC, id DESC);

-- +goose Down
DROP TABLE IF EXISTS captures;
