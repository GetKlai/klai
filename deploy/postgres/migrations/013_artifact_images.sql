-- 013: artifact_images linktabel voor per-connector image cleanup.
--
-- SPEC-CONNECTOR-DELETE-LIFECYCLE-001 REQ-06.
--
-- Background: image keys in Garage S3 are content-addressed
-- (``{org}/images/{kb_slug}/{sha256}.{ext}``). Without an artifact↔image
-- link table per-connector cleanup is impossible — the SHA256 dedup means
-- one S3 key can be referenced by many artifacts, and the URL alone is
-- not enough to know whether a key has become orphan.
--
-- This table is the bookkeeping layer:
--   - one row per (artifact_id, s3_key) pair
--   - FK CASCADE on artifact_id keeps the table consistent automatically
--     when artifacts are hard-deleted
--   - index on content_hash enables the refcount check used by
--     ``pg_store.delete_connector_image_refs``: "is this content_hash
--     referenced by any artifact OUTSIDE the deleted set?" -> if not,
--     the key is orphan and must be removed from S3
--
-- No backfill in this migration: rows are written by the ingest pipeline
-- going forward (REQ-06.2). Historical artifacts continue to leak orphan
-- images until a separate one-shot backfill scans
-- ``knowledge.artifacts.extra->>'image_urls'`` and seeds rows.

CREATE TABLE IF NOT EXISTS knowledge.artifact_images (
    artifact_id  UUID NOT NULL REFERENCES knowledge.artifacts(id) ON DELETE CASCADE,
    s3_key       TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    PRIMARY KEY (artifact_id, s3_key)
);

CREATE INDEX IF NOT EXISTS ix_artifact_images_content_hash
    ON knowledge.artifact_images (content_hash);

COMMENT ON TABLE  knowledge.artifact_images IS
    'SPEC-CONNECTOR-DELETE-LIFECYCLE-001 REQ-06: per-artifact S3 image bookkeeping for connector-scoped cleanup. SHA256 content addressing means one key can be shared, so deletes are refcounted on content_hash.';
COMMENT ON COLUMN knowledge.artifact_images.content_hash IS
    'SHA-256 hex of the image bytes (= the basename component of s3_key without extension). Indexed because per-connector cleanup queries refcount on this column.';
COMMENT ON COLUMN knowledge.artifact_images.s3_key IS
    'Full S3 object key including org_id prefix and kb_slug subfolder, e.g. "368884765035593759/images/support/abc123def456.png".';
