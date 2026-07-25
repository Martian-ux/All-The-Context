PRAGMA foreign_keys = ON;

ALTER TABLE source_blobs
    ADD COLUMN storage_kind TEXT NOT NULL DEFAULT 'inline'
    CHECK(storage_kind IN ('inline', 'chunked'));

CREATE TABLE IF NOT EXISTS source_blob_chunks (
    content_hash TEXT NOT NULL
        REFERENCES source_blobs(content_hash) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL CHECK(chunk_index >= 0),
    content BLOB NOT NULL,
    byte_size INTEGER NOT NULL CHECK(byte_size BETWEEN 1 AND 8388608),
    CHECK(length(content) = byte_size),
    PRIMARY KEY(content_hash, chunk_index)
);

WITH RECURSIVE chunk_numbers(content_hash, chunk_index, byte_size) AS (
    SELECT content_hash, 0, byte_size
    FROM source_blobs
    WHERE length(content) > 8388608
    UNION ALL
    SELECT content_hash, chunk_index + 1, byte_size
    FROM chunk_numbers
    WHERE (chunk_index + 1) * 8388608 < byte_size
)
INSERT INTO source_blob_chunks(content_hash, chunk_index, content, byte_size)
SELECT
    chunk_numbers.content_hash,
    chunk_numbers.chunk_index,
    substr(
        source_blobs.content,
        chunk_numbers.chunk_index * 8388608 + 1,
        8388608
    ),
    length(
        substr(
            source_blobs.content,
            chunk_numbers.chunk_index * 8388608 + 1,
            8388608
        )
    )
FROM chunk_numbers
JOIN source_blobs USING(content_hash);

UPDATE source_blobs
SET content = X'', storage_kind = 'chunked'
WHERE length(content) > 8388608;

UPDATE vaults SET schema_version = 7 WHERE schema_version < 7;
