-- +goose Up
ALTER TABLE activity ADD COLUMN IF NOT EXISTS src TEXT NOT NULL DEFAULT '';

CREATE INDEX IF NOT EXISTS idx_activity_src_created_id
    ON activity (src, ts_created DESC, id DESC);

-- +goose Down
DROP INDEX idx_activity_src_created_id;
ALTER TABLE activity DROP COLUMN src;
