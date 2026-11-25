-- +goose Up
-- One row per persisted request/response capture. id is the activity row's
-- id, so a collector can join the two tables. Sensitive headers are redacted
-- before a row is written. Bodies are UTF-8 text (anything else is stored as
-- "base64:" + base64). prompt_text and response_text are the readable parts
-- pulled out of the bodies: the last user message and the model's answer.
CREATE TABLE IF NOT EXISTS captures (
    id BIGINT PRIMARY KEY,
    ts_created BIGINT NOT NULL,
    model_id TEXT NOT NULL DEFAULT '',
    req_path TEXT NOT NULL DEFAULT '',
    resp_status_code INTEGER NOT NULL DEFAULT 0,
    req_headers_json TEXT NOT NULL DEFAULT '',
    req_body TEXT NOT NULL DEFAULT '',
    resp_headers_json TEXT NOT NULL DEFAULT '',
    resp_body TEXT NOT NULL DEFAULT '',
    prompt_text TEXT NOT NULL DEFAULT '',
    response_text TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_captures_created_id
    ON captures (ts_created DESC, id DESC);

CREATE INDEX IF NOT EXISTS idx_captures_model_id
    ON captures (model_id, id);

-- +goose Down
DROP TABLE IF EXISTS captures;
