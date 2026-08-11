"""SQLite schema for immutable intelligence documents and derived events."""

SCHEMA_VERSION = 16

PERFORMANCE_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_document_chunks_document_artifact
    ON document_chunks(document_id, artifact_id, sequence_no, chunk_id);
CREATE INDEX IF NOT EXISTS idx_document_tables_document_artifact
    ON document_tables(
        document_id, artifact_id, page_number, sequence_no, table_id
    );
CREATE INDEX IF NOT EXISTS idx_documents_source_source_id_published
    ON documents(source, source_id, published_at, id);
CREATE INDEX IF NOT EXISTS idx_documents_source_revision_published
    ON documents(source, revision_of, published_at, id);
"""

MIGRATION_V1 = """
CREATE TABLE IF NOT EXISTS schema_meta (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    source_id TEXT NOT NULL,
    title TEXT NOT NULL,
    published_at TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    effective_at TEXT NOT NULL,
    revised_at TEXT,
    revision_of TEXT,
    source_url TEXT NOT NULL,
    mime_type TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    raw_path TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'collected',
    UNIQUE(source, source_id, content_hash)
);
CREATE INDEX IF NOT EXISTS idx_documents_effective ON documents(effective_at);
CREATE INDEX IF NOT EXISTS idx_documents_source_time ON documents(source, published_at);
CREATE TABLE IF NOT EXISTS ingestion_runs (
    run_id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    cursor_in TEXT,
    cursor_out TEXT,
    fetched INTEGER NOT NULL DEFAULT 0,
    inserted INTEGER NOT NULL DEFAULT 0,
    error TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS source_cursors (
    source TEXT PRIMARY KEY,
    cursor TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    event_id TEXT PRIMARY KEY,
    document_id INTEGER NOT NULL REFERENCES documents(id),
    event_type TEXT NOT NULL,
    direction REAL NOT NULL,
    strength REAL NOT NULL,
    confidence REAL NOT NULL,
    novelty REAL NOT NULL,
    horizon_days INTEGER NOT NULL,
    published_at TEXT NOT NULL,
    effective_at TEXT NOT NULL,
    evidence TEXT NOT NULL,
    extraction_method TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_events_effective ON events(effective_at);
CREATE TABLE IF NOT EXISTS event_entities (
    event_id TEXT NOT NULL REFERENCES events(event_id),
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    entity_name TEXT NOT NULL DEFAULT '',
    industry TEXT NOT NULL DEFAULT '',
    confidence REAL NOT NULL,
    PRIMARY KEY(event_id, entity_type, entity_id)
);
CREATE INDEX IF NOT EXISTS idx_event_entities_id ON event_entities(entity_type, entity_id);
CREATE TABLE IF NOT EXISTS quality_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    source TEXT NOT NULL,
    metric TEXT NOT NULL,
    value REAL NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    measured_at TEXT NOT NULL
);
"""

MIGRATION_V2 = """
CREATE TABLE IF NOT EXISTS intelligence_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS document_availability (
    document_id INTEGER PRIMARY KEY REFERENCES documents(id),
    source_recorded_at TEXT,
    research_available_at TEXT NOT NULL,
    availability_provenance TEXT NOT NULL
      CHECK (availability_provenance IN
        ('observed', 'reconstructed_rec_time', 'reconstructed_next_open')),
    historical_cutoff TEXT NOT NULL,
    created_at TEXT NOT NULL,
    CHECK (
        availability_provenance <> 'reconstructed_rec_time'
        OR (
            source_recorded_at IS NOT NULL
            AND research_available_at = source_recorded_at
        )
    )
);

CREATE TABLE IF NOT EXISTS backfill_partitions (
    source TEXT NOT NULL,
    partition_start TEXT NOT NULL,
    partition_end TEXT NOT NULL,
    next_offset INTEGER NOT NULL DEFAULT 0 CHECK (next_offset >= 0),
    status TEXT NOT NULL
      CHECK (status IN
        ('pending', 'running', 'complete', 'failed_retryable',
         'failed_terminal', 'failed_overflow')),
    fetched INTEGER NOT NULL DEFAULT 0 CHECK (fetched >= 0),
    inserted INTEGER NOT NULL DEFAULT 0 CHECK (inserted >= 0),
    b_share_filtered INTEGER NOT NULL DEFAULT 0 CHECK (b_share_filtered >= 0),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    error TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL,
    PRIMARY KEY (source, partition_start, partition_end),
    CHECK (partition_start <= partition_end)
);

CREATE TABLE IF NOT EXISTS document_artifacts (
    artifact_id TEXT PRIMARY KEY,
    document_id INTEGER NOT NULL REFERENCES documents(id),
    artifact_type TEXT NOT NULL CHECK (artifact_type IN ('pdf', 'parsed')),
    content_hash TEXT NOT NULL,
    storage_uri TEXT NOT NULL,
    mime_type TEXT NOT NULL,
    byte_size INTEGER NOT NULL CHECK (byte_size >= 0),
    parser_version TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL
      CHECK (status IN
        ('queued', 'downloaded', 'parsed', 'ocr_required', 'ocr_failed',
         'failed_retryable', 'failed_terminal')),
    error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (document_id, artifact_type, content_hash, parser_version),
    UNIQUE (artifact_id, document_id)
);

CREATE TABLE IF NOT EXISTS document_chunks (
    chunk_id TEXT PRIMARY KEY,
    document_id INTEGER NOT NULL REFERENCES documents(id),
    artifact_id TEXT NOT NULL,
    sequence_no INTEGER NOT NULL CHECK (sequence_no >= 0),
    page_number INTEGER NOT NULL CHECK (page_number >= 0),
    section TEXT NOT NULL DEFAULT '',
    bbox_json TEXT NOT NULL CHECK (json_valid(bbox_json)),
    text TEXT NOT NULL,
    text_hash TEXT NOT NULL,
    ocr_used INTEGER NOT NULL DEFAULT 0 CHECK (ocr_used IN (0, 1)),
    ocr_confidence REAL
      CHECK (ocr_confidence IS NULL OR ocr_confidence BETWEEN 0.0 AND 1.0),
    parser_version TEXT NOT NULL,
    UNIQUE (artifact_id, sequence_no, parser_version),
    UNIQUE (chunk_id, document_id),
    UNIQUE (chunk_id, document_id, page_number),
    FOREIGN KEY (artifact_id, document_id)
      REFERENCES document_artifacts(artifact_id, document_id)
);

CREATE TABLE IF NOT EXISTS document_tables (
    table_id TEXT PRIMARY KEY,
    document_id INTEGER NOT NULL REFERENCES documents(id),
    artifact_id TEXT NOT NULL,
    page_number INTEGER NOT NULL CHECK (page_number >= 0),
    sequence_no INTEGER NOT NULL CHECK (sequence_no >= 0),
    bbox_json TEXT NOT NULL CHECK (json_valid(bbox_json)),
    cells_json TEXT NOT NULL CHECK (json_valid(cells_json)),
    parser_version TEXT NOT NULL,
    UNIQUE (artifact_id, page_number, sequence_no, parser_version),
    FOREIGN KEY (artifact_id, document_id)
      REFERENCES document_artifacts(artifact_id, document_id)
);

CREATE TABLE IF NOT EXISTS semantic_runs (
    run_id TEXT PRIMARY KEY,
    document_id INTEGER NOT NULL REFERENCES documents(id),
    artifact_hash TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    taxonomy_version TEXT NOT NULL,
    parser_version TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    output_hash TEXT,
    output_uri TEXT,
    status TEXT NOT NULL
      CHECK (status IN
        ('running', 'succeeded', 'no_event', 'failed_retryable',
         'failed_terminal', 'budget_deferred', 'unavailable')),
    input_tokens INTEGER CHECK (input_tokens IS NULL OR input_tokens >= 0),
    output_tokens INTEGER CHECK (output_tokens IS NULL OR output_tokens >= 0),
    latency_ms INTEGER CHECK (latency_ms IS NULL OR latency_ms >= 0),
    cost_microunits INTEGER
      CHECK (cost_microunits IS NULL OR cost_microunits >= 0),
    error TEXT NOT NULL DEFAULT '',
    started_at TEXT NOT NULL,
    finished_at TEXT,
    UNIQUE (
      document_id, artifact_hash, provider, model, prompt_version,
      schema_version, taxonomy_version, parser_version, input_hash
    ),
    UNIQUE (run_id, document_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_events_id_document_unique
    ON events(event_id, document_id);

CREATE TABLE IF NOT EXISTS event_candidates (
    candidate_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    document_id INTEGER NOT NULL REFERENCES documents(id),
    event_index INTEGER NOT NULL CHECK (event_index >= 0),
    event_type TEXT NOT NULL,
    lifecycle TEXT NOT NULL,
    payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
    validation_status TEXT NOT NULL
      CHECK (validation_status IN ('pending', 'canonical', 'quarantined')),
    validation_errors_json TEXT NOT NULL DEFAULT '[]'
      CHECK (json_valid(validation_errors_json)),
    canonical_event_id TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (run_id, event_index),
    UNIQUE (candidate_id, document_id),
    CHECK (
        (
            validation_status = 'canonical'
            AND canonical_event_id IS NOT NULL
        )
        OR (
            validation_status IN ('pending', 'quarantined')
            AND canonical_event_id IS NULL
        )
    ),
    FOREIGN KEY (run_id, document_id)
      REFERENCES semantic_runs(run_id, document_id),
    FOREIGN KEY (canonical_event_id, document_id)
      REFERENCES events(event_id, document_id)
);

CREATE TABLE IF NOT EXISTS event_evidence (
    candidate_id TEXT NOT NULL,
    document_id INTEGER NOT NULL REFERENCES documents(id),
    evidence_id TEXT NOT NULL,
    chunk_id TEXT NOT NULL,
    page_number INTEGER NOT NULL CHECK (page_number >= 0),
    start_char INTEGER NOT NULL CHECK (start_char >= 0),
    end_char INTEGER NOT NULL CHECK (end_char >= start_char),
    quote TEXT NOT NULL,
    normalized_quote_hash TEXT NOT NULL,
    PRIMARY KEY (candidate_id, evidence_id),
    FOREIGN KEY (candidate_id, document_id)
      REFERENCES event_candidates(candidate_id, document_id),
    FOREIGN KEY (chunk_id, document_id, page_number)
      REFERENCES document_chunks(chunk_id, document_id, page_number)
);

CREATE TABLE IF NOT EXISTS event_facts (
    event_id TEXT NOT NULL REFERENCES events(event_id),
    fact_name TEXT NOT NULL,
    ordinal INTEGER NOT NULL DEFAULT 0 CHECK (ordinal >= 0),
    raw_value TEXT,
    numeric_value TEXT,
    text_value TEXT,
    unit TEXT,
    currency TEXT,
    period TEXT,
    evidence_ids_json TEXT NOT NULL CHECK (json_valid(evidence_ids_json)),
    provenance TEXT NOT NULL,
    PRIMARY KEY (event_id, fact_name, ordinal)
);

CREATE TABLE IF NOT EXISTS event_scores (
    event_id TEXT PRIMARY KEY REFERENCES events(event_id),
    relevance REAL NOT NULL CHECK (relevance BETWEEN 0.0 AND 1.0),
    novelty REAL NOT NULL CHECK (novelty BETWEEN 0.0 AND 1.0),
    materiality REAL
      CHECK (materiality IS NULL OR materiality BETWEEN 0.0 AND 1.0),
    certainty REAL NOT NULL CHECK (certainty BETWEEN 0.0 AND 1.0),
    source_credibility REAL NOT NULL
      CHECK (source_credibility BETWEEN 0.0 AND 1.0),
    direction REAL NOT NULL CHECK (direction BETWEEN -1.0 AND 1.0),
    confidence REAL NOT NULL CHECK (confidence BETWEEN 0.0 AND 1.0),
    scoring_version TEXT NOT NULL,
    inputs_json TEXT NOT NULL CHECK (json_valid(inputs_json)),
    scored_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS event_relations (
    source_event_id TEXT NOT NULL REFERENCES events(event_id),
    target_event_id TEXT NOT NULL REFERENCES events(event_id),
    relation_type TEXT NOT NULL
      CHECK (relation_type IN
        ('revises', 'cancels', 'completes', 'duplicates', 'supersedes')),
    available_at TEXT NOT NULL,
    PRIMARY KEY (source_event_id, target_event_id, relation_type)
);

CREATE INDEX IF NOT EXISTS idx_backfill_partitions_status_date
    ON backfill_partitions(status, partition_start, partition_end);
CREATE INDEX IF NOT EXISTS idx_document_artifacts_status_document
    ON document_artifacts(status, document_id);
CREATE INDEX IF NOT EXISTS idx_semantic_runs_status_document
    ON semantic_runs(status, document_id);
CREATE INDEX IF NOT EXISTS idx_event_candidates_validation
    ON event_candidates(validation_status, document_id);
CREATE INDEX IF NOT EXISTS idx_event_evidence_chunk
    ON event_evidence(chunk_id);
"""

MIGRATION_V3 = """
ALTER TABLE backfill_partitions
    ADD COLUMN request_limit INTEGER NOT NULL DEFAULT 0
    CHECK (request_limit >= 0);

CREATE TABLE IF NOT EXISTS backfill_universe_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    content_hash TEXT NOT NULL UNIQUE,
    security_count INTEGER NOT NULL CHECK (security_count >= 0),
    list_statuses TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS backfill_universe_members (
    snapshot_id TEXT NOT NULL
      REFERENCES backfill_universe_snapshots(snapshot_id),
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    ts_code TEXT NOT NULL,
    PRIMARY KEY (snapshot_id, ordinal),
    UNIQUE (snapshot_id, ts_code)
);

CREATE TABLE IF NOT EXISTS backfill_partition_universes (
    source TEXT NOT NULL,
    partition_start TEXT NOT NULL,
    partition_end TEXT NOT NULL,
    snapshot_id TEXT NOT NULL
      REFERENCES backfill_universe_snapshots(snapshot_id),
    created_at TEXT NOT NULL,
    PRIMARY KEY (source, partition_start, partition_end),
    UNIQUE (source, partition_start, partition_end, snapshot_id),
    FOREIGN KEY (source, partition_start, partition_end)
      REFERENCES backfill_partitions(
        source, partition_start, partition_end
      )
);

CREATE TABLE IF NOT EXISTS backfill_partition_items (
    source TEXT NOT NULL,
    partition_start TEXT NOT NULL,
    partition_end TEXT NOT NULL,
    snapshot_id TEXT NOT NULL,
    ts_code TEXT NOT NULL,
    next_offset INTEGER NOT NULL DEFAULT 0 CHECK (next_offset >= 0),
    request_limit INTEGER NOT NULL CHECK (request_limit > 0),
    status TEXT NOT NULL
      CHECK (status IN
        ('pending', 'running', 'complete', 'failed_retryable',
         'failed_terminal', 'failed_overflow')),
    fetched INTEGER NOT NULL DEFAULT 0 CHECK (fetched >= 0),
    inserted INTEGER NOT NULL DEFAULT 0 CHECK (inserted >= 0),
    b_share_filtered INTEGER NOT NULL DEFAULT 0
      CHECK (b_share_filtered >= 0),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    error TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL,
    PRIMARY KEY (
      source, partition_start, partition_end, ts_code
    ),
    FOREIGN KEY (
      source, partition_start, partition_end, snapshot_id
    ) REFERENCES backfill_partition_universes(
      source, partition_start, partition_end, snapshot_id
    ),
    FOREIGN KEY (snapshot_id, ts_code)
      REFERENCES backfill_universe_members(snapshot_id, ts_code)
);

CREATE INDEX IF NOT EXISTS idx_backfill_partition_items_status
    ON backfill_partition_items(
      source, partition_start, partition_end, status, ts_code
    );
CREATE INDEX IF NOT EXISTS idx_backfill_universe_members_code
    ON backfill_universe_members(ts_code, snapshot_id);
"""

MIGRATION_V4 = """
ALTER TABLE documents
    ADD COLUMN queue_priority INTEGER NOT NULL DEFAULT 100
    CHECK (queue_priority >= 0);
ALTER TABLE documents
    ADD COLUMN live_observed INTEGER NOT NULL DEFAULT 0
    CHECK (live_observed IN (0, 1));
UPDATE documents
SET queue_priority=CASE
      WHEN json_extract(metadata_json, '$.ingestion_mode')='history'
      THEN 10 ELSE 100 END,
    live_observed=CASE
      WHEN json_extract(metadata_json, '$.ingestion_mode')='live'
      THEN 1 ELSE 0 END;
CREATE INDEX IF NOT EXISTS idx_documents_queue_priority
    ON documents(status, queue_priority DESC, published_at, id);

ALTER TABLE backfill_partitions
    ADD COLUMN completion_strategy_version INTEGER NOT NULL DEFAULT 0
    CHECK (completion_strategy_version >= 0);
ALTER TABLE backfill_partitions
    ADD COLUMN probe_manifest_version INTEGER NOT NULL DEFAULT 0
    CHECK (probe_manifest_version >= 0);
ALTER TABLE backfill_universe_members
    ADD COLUMN security_type TEXT NOT NULL DEFAULT 'stock';
ALTER TABLE backfill_universe_members
    ADD COLUMN list_date TEXT NOT NULL DEFAULT '';
ALTER TABLE backfill_universe_members
    ADD COLUMN delist_date TEXT NOT NULL DEFAULT '';
ALTER TABLE backfill_universe_members
    ADD COLUMN listing_status TEXT NOT NULL DEFAULT '';

CREATE TABLE IF NOT EXISTS backfill_partition_probe_documents (
    source TEXT NOT NULL,
    partition_start TEXT NOT NULL,
    partition_end TEXT NOT NULL,
    source_id TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    ts_code TEXT NOT NULL,
    document_id INTEGER NOT NULL REFERENCES documents(id),
    created_at TEXT NOT NULL,
    PRIMARY KEY (
      source, partition_start, partition_end, source_id, content_hash,
      ts_code
    ),
    FOREIGN KEY (source, partition_start, partition_end)
      REFERENCES backfill_partitions(
        source, partition_start, partition_end
      )
);
CREATE INDEX IF NOT EXISTS idx_backfill_probe_document
    ON backfill_partition_probe_documents(document_id);

UPDATE backfill_partitions
SET status='pending', next_offset=0, fetched=0, inserted=0,
    b_share_filtered=0, error='', completion_strategy_version=0,
    probe_manifest_version=0
WHERE status='complete'
   OR EXISTS (
       SELECT 1 FROM backfill_partition_universes u
       WHERE u.source=backfill_partitions.source
         AND u.partition_start=backfill_partitions.partition_start
         AND u.partition_end=backfill_partitions.partition_end
   );
DELETE FROM backfill_partition_items;
DELETE FROM backfill_partition_universes;
DELETE FROM backfill_universe_members;
DELETE FROM backfill_universe_snapshots;
"""

MIGRATION_V5 = """
CREATE TABLE IF NOT EXISTS source_retry_windows (
    source TEXT PRIMARY KEY,
    unresolved_day TEXT NOT NULL
      CHECK (
        length(unresolved_day)=10
        AND substr(unresolved_day, 5, 1)='-'
        AND substr(unresolved_day, 8, 1)='-'
      ),
    reason TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 1 CHECK (attempts >= 1)
);

CREATE TABLE IF NOT EXISTS document_security_links (
    document_id INTEGER NOT NULL REFERENCES documents(id),
    ts_code TEXT NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    provenance TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (document_id, ts_code)
);
CREATE INDEX IF NOT EXISTS idx_document_security_links_code
    ON document_security_links(ts_code, document_id);

CREATE TABLE IF NOT EXISTS backfill_source_universes (
    source TEXT PRIMARY KEY,
    snapshot_id TEXT NOT NULL
      REFERENCES backfill_universe_snapshots(snapshot_id),
    created_at TEXT NOT NULL
);

INSERT OR IGNORE INTO document_security_links(
    document_id, ts_code, name, provenance, created_at, updated_at
)
SELECT
    d.id,
    upper(trim(json_extract(link.value, '$.ts_code'))),
    coalesce(trim(json_extract(link.value, '$.name')), ''),
    'legacy_metadata',
    d.first_seen_at,
    d.first_seen_at
FROM documents d
JOIN json_each(d.metadata_json, '$.security_links') AS link
WHERE json_type(d.metadata_json, '$.security_links')='array'
  AND json_type(link.value)='object'
  AND trim(coalesce(json_extract(link.value, '$.ts_code'), ''))<>'';

INSERT OR IGNORE INTO document_security_links(
    document_id, ts_code, name, provenance, created_at, updated_at
)
SELECT
    d.id,
    upper(trim(code.value)),
    '',
    'legacy_metadata',
    d.first_seen_at,
    d.first_seen_at
FROM documents d
JOIN json_each(d.metadata_json, '$.security_codes') AS code
WHERE json_type(d.metadata_json, '$.security_codes')='array'
  AND trim(coalesce(code.value, ''))<>'';

INSERT OR IGNORE INTO document_security_links(
    document_id, ts_code, name, provenance, created_at, updated_at
)
SELECT
    d.id,
    upper(trim(json_extract(d.metadata_json, '$.ts_code'))),
    coalesce(trim(json_extract(d.metadata_json, '$.name')), ''),
    'legacy_metadata',
    d.first_seen_at,
    d.first_seen_at
FROM documents d
WHERE trim(coalesce(json_extract(d.metadata_json, '$.ts_code'), ''))<>'';

INSERT OR IGNORE INTO document_security_links(
    document_id, ts_code, name, provenance, created_at, updated_at
)
SELECT
    p.document_id,
    upper(trim(p.ts_code)),
    coalesce(
      (
        SELECT min(trim(json_extract(link.value, '$.name')))
        FROM json_each(
          CASE WHEN json_valid(d.metadata_json)
               THEN d.metadata_json ELSE '{}' END,
          '$.security_links'
        ) AS link
        WHERE upper(trim(json_extract(link.value, '$.ts_code')))
              =upper(trim(p.ts_code))
          AND trim(coalesce(
                json_extract(link.value, '$.name'), ''
              ))<>''
      ),
      CASE
        WHEN upper(trim(coalesce(
               json_extract(d.metadata_json, '$.ts_code'), ''
             )))=upper(trim(p.ts_code))
        THEN trim(coalesce(
               json_extract(d.metadata_json, '$.name'), ''
             ))
        ELSE ''
      END,
      ''
    ),
    'legacy_probe',
    p.created_at,
    p.created_at
FROM backfill_partition_probe_documents p
JOIN documents d ON d.id=p.document_id
WHERE trim(p.ts_code)<>'';

DELETE FROM backfill_partition_probe_documents
WHERE EXISTS (
    SELECT 1
    FROM backfill_partition_universes u
    WHERE u.source=backfill_partition_probe_documents.source
      AND u.partition_start=
          backfill_partition_probe_documents.partition_start
      AND u.partition_end=
          backfill_partition_probe_documents.partition_end
);

UPDATE backfill_partitions
SET status='pending', next_offset=0, fetched=0, inserted=0,
    b_share_filtered=0, error='', completion_strategy_version=0,
    probe_manifest_version=0
WHERE EXISTS (
    SELECT 1
    FROM backfill_partition_universes u
    WHERE u.source=backfill_partitions.source
      AND u.partition_start=backfill_partitions.partition_start
      AND u.partition_end=backfill_partitions.partition_end
);

DELETE FROM backfill_partition_items;
DELETE FROM backfill_partition_universes;
DELETE FROM backfill_universe_members;
DELETE FROM backfill_universe_snapshots;
"""

MIGRATION_V6 = """
ALTER TABLE documents
    ADD COLUMN link_revision INTEGER NOT NULL DEFAULT 0
    CHECK (link_revision >= 0);
ALTER TABLE documents
    ADD COLUMN extracted_link_revision INTEGER NOT NULL DEFAULT 0
    CHECK (extracted_link_revision >= 0);
ALTER TABLE ingestion_runs
    ADD COLUMN generation INTEGER NOT NULL DEFAULT 0
    CHECK (generation >= 0);
ALTER TABLE ingestion_runs
    ADD COLUMN owner TEXT NOT NULL DEFAULT '';
ALTER TABLE source_retry_windows
    ADD COLUMN generation INTEGER NOT NULL DEFAULT 0
    CHECK (generation >= 0);
ALTER TABLE source_retry_windows
    ADD COLUMN owner TEXT NOT NULL DEFAULT '';
ALTER TABLE backfill_partitions
    ADD COLUMN job_id TEXT NOT NULL DEFAULT '';

CREATE TABLE IF NOT EXISTS announcement_security_catalog (
    source TEXT NOT NULL,
    ts_code TEXT NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    provenance TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    observations INTEGER NOT NULL DEFAULT 1
      CHECK (observations >= 1),
    PRIMARY KEY (source, ts_code)
);
CREATE INDEX IF NOT EXISTS idx_announcement_catalog_code
    ON announcement_security_catalog(ts_code, source);

CREATE TABLE IF NOT EXISTS backfill_jobs (
    job_id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    completion_strategy_version INTEGER NOT NULL,
    config_hash TEXT NOT NULL,
    request_limit INTEGER NOT NULL CHECK (request_limit > 0),
    verification_required INTEGER NOT NULL
      CHECK (verification_required >= 1),
    status TEXT NOT NULL
      CHECK (status IN ('running', 'partial', 'complete', 'failed')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (
      source, start_date, end_date,
      completion_strategy_version, config_hash
    ),
    CHECK (start_date <= end_date)
);

CREATE TABLE IF NOT EXISTS backfill_job_universes (
    job_id TEXT PRIMARY KEY REFERENCES backfill_jobs(job_id),
    snapshot_id TEXT NOT NULL
      REFERENCES backfill_universe_snapshots(snapshot_id),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS backfill_partition_verification_state (
    job_id TEXT NOT NULL REFERENCES backfill_jobs(job_id),
    source TEXT NOT NULL,
    partition_start TEXT NOT NULL,
    partition_end TEXT NOT NULL,
    rounds_total INTEGER NOT NULL DEFAULT 0
      CHECK (rounds_total >= 0),
    stable_rounds INTEGER NOT NULL DEFAULT 0
      CHECK (stable_rounds >= 0),
    last_probe_hash TEXT NOT NULL DEFAULT '',
    last_new_documents INTEGER NOT NULL DEFAULT 0
      CHECK (last_new_documents >= 0),
    last_new_security_codes INTEGER NOT NULL DEFAULT 0
      CHECK (last_new_security_codes >= 0),
    updated_at TEXT NOT NULL,
    PRIMARY KEY (
      job_id, source, partition_start, partition_end
    )
);

CREATE TABLE IF NOT EXISTS backfill_verification_rounds (
    job_id TEXT NOT NULL REFERENCES backfill_jobs(job_id),
    source TEXT NOT NULL,
    partition_start TEXT NOT NULL,
    partition_end TEXT NOT NULL,
    round_no INTEGER NOT NULL CHECK (round_no >= 1),
    probe_hash TEXT NOT NULL,
    probe_documents INTEGER NOT NULL
      CHECK (probe_documents >= 0),
    probe_security_codes INTEGER NOT NULL
      CHECK (probe_security_codes >= 0),
    new_documents INTEGER NOT NULL
      CHECK (new_documents >= 0),
    new_security_codes INTEGER NOT NULL
      CHECK (new_security_codes >= 0),
    stable_rounds INTEGER NOT NULL CHECK (stable_rounds >= 0),
    created_at TEXT NOT NULL,
    PRIMARY KEY (
      job_id, source, partition_start, partition_end, round_no
    )
);

CREATE TABLE IF NOT EXISTS source_ingestion_leases (
    source TEXT PRIMARY KEY,
    generation INTEGER NOT NULL CHECK (generation >= 1),
    owner TEXT NOT NULL,
    lease_until TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_backfill_jobs_source_range
    ON backfill_jobs(source, start_date, end_date, updated_at);
CREATE INDEX IF NOT EXISTS idx_backfill_verification_partition
    ON backfill_verification_rounds(
      source, partition_start, partition_end, round_no
    );

INSERT OR IGNORE INTO document_security_links(
    document_id, ts_code, name, provenance, created_at, updated_at
)
SELECT
    p.document_id,
    upper(trim(p.ts_code)),
    coalesce(
      (
        SELECT min(trim(json_extract(link.value, '$.name')))
        FROM json_each(
          CASE WHEN json_valid(d.metadata_json)
               THEN d.metadata_json ELSE '{}' END,
          '$.security_links'
        ) AS link
        WHERE upper(trim(json_extract(link.value, '$.ts_code')))
              =upper(trim(p.ts_code))
          AND trim(coalesce(
                json_extract(link.value, '$.name'), ''
              ))<>''
      ),
      ''
    ),
    'legacy_probe',
    p.created_at,
    p.created_at
FROM backfill_partition_probe_documents p
JOIN documents d ON d.id=p.document_id
WHERE trim(p.ts_code)<>'';

INSERT INTO announcement_security_catalog(
    source, ts_code, name, provenance,
    first_seen_at, last_seen_at, observations
)
SELECT
    d.source,
    upper(trim(l.ts_code)),
    coalesce(min(nullif(trim(l.name), '')), ''),
    coalesce(min(nullif(trim(l.provenance), '')), 'document_link'),
    min(l.created_at),
    max(l.updated_at),
    count(*)
FROM document_security_links l
JOIN documents d ON d.id=l.document_id
WHERE d.source='tushare_announcement'
  AND trim(l.ts_code)<>''
GROUP BY d.source, upper(trim(l.ts_code))
ON CONFLICT(source, ts_code) DO UPDATE SET
    name=CASE
      WHEN announcement_security_catalog.name=''
      THEN excluded.name
      WHEN excluded.name=''
      THEN announcement_security_catalog.name
      WHEN excluded.name < announcement_security_catalog.name
      THEN excluded.name
      ELSE announcement_security_catalog.name
    END,
    provenance=CASE
      WHEN excluded.provenance
           < announcement_security_catalog.provenance
      THEN excluded.provenance
      ELSE announcement_security_catalog.provenance
    END,
    first_seen_at=min(
      announcement_security_catalog.first_seen_at,
      excluded.first_seen_at
    ),
    last_seen_at=max(
      announcement_security_catalog.last_seen_at,
      excluded.last_seen_at
    ),
    observations=announcement_security_catalog.observations
                 + excluded.observations;

UPDATE documents
SET link_revision=(
      SELECT COUNT(*)
      FROM document_security_links links
      WHERE links.document_id=documents.id
    ),
    extracted_link_revision=0,
    status=CASE
      WHEN status IN ('processed', 'no_event')
       AND EXISTS (
         SELECT 1
         FROM document_security_links links
         WHERE links.document_id=documents.id
       )
      THEN 'collected'
      ELSE status
    END;

INSERT INTO source_retry_windows(
    source, unresolved_day, reason,
    first_seen_at, last_seen_at, attempts,
    generation, owner
)
SELECT
    source,
    substr(day_key, 1, 4) || '-'
      || substr(day_key, 5, 2) || '-'
      || substr(day_key, 7, 2),
    'legacy_day_saturated',
    min(started_at),
    max(coalesce(finished_at, started_at)),
    count(*),
    0,
    'migration'
FROM (
    SELECT
      source,
      started_at,
      finished_at,
      substr(
        error,
        instr(error, 'day_saturated:') + 14,
        8
      ) AS day_key
    FROM ingestion_runs
    WHERE instr(error, 'day_saturated:') > 0
)
WHERE length(day_key)=8
  AND day_key NOT GLOB '*[^0-9]*'
GROUP BY source, day_key
ON CONFLICT(source) DO UPDATE SET
    unresolved_day=CASE
      WHEN excluded.unresolved_day
           < source_retry_windows.unresolved_day
      THEN excluded.unresolved_day
      ELSE source_retry_windows.unresolved_day
    END,
    reason='legacy_day_saturated',
    first_seen_at=min(
      source_retry_windows.first_seen_at,
      excluded.first_seen_at
    ),
    last_seen_at=max(
      source_retry_windows.last_seen_at,
      excluded.last_seen_at
    ),
    attempts=source_retry_windows.attempts+excluded.attempts;

DELETE FROM backfill_partition_probe_documents;
DELETE FROM backfill_partition_items;
DELETE FROM backfill_partition_universes;
DELETE FROM backfill_source_universes;
DELETE FROM backfill_universe_members;
DELETE FROM backfill_universe_snapshots;

UPDATE backfill_partitions
SET next_offset=0, status='pending', fetched=0, inserted=0,
    b_share_filtered=0, error='', job_id='',
    completion_strategy_version=0, probe_manifest_version=0,
    updated_at=CURRENT_TIMESTAMP;
"""

MIGRATION_V7 = """
ALTER TABLE backfill_partitions
    ADD COLUMN evidence_config_hash TEXT NOT NULL DEFAULT '';

CREATE TABLE IF NOT EXISTS backfill_job_partition_refs (
    job_id TEXT NOT NULL REFERENCES backfill_jobs(job_id),
    source TEXT NOT NULL,
    partition_start TEXT NOT NULL,
    partition_end TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (
      job_id, source, partition_start, partition_end
    ),
    FOREIGN KEY (source, partition_start, partition_end)
      REFERENCES backfill_partitions(
        source, partition_start, partition_end
      )
);
CREATE INDEX IF NOT EXISTS idx_backfill_job_partition_refs_partition
    ON backfill_job_partition_refs(
      source, partition_start, partition_end, job_id
    );

INSERT OR IGNORE INTO backfill_job_partition_refs(
    job_id, source, partition_start, partition_end, created_at
)
SELECT
    p.job_id,
    p.source,
    p.partition_start,
    p.partition_end,
    p.updated_at
FROM backfill_partitions p
JOIN backfill_jobs j ON j.job_id=p.job_id
WHERE trim(p.job_id)<>'';

INSERT OR IGNORE INTO backfill_job_partition_refs(
    job_id, source, partition_start, partition_end, created_at
)
SELECT
    v.job_id,
    v.source,
    v.partition_start,
    v.partition_end,
    v.updated_at
FROM backfill_partition_verification_state v
JOIN backfill_partitions p
  ON p.source=v.source
 AND p.partition_start=v.partition_start
 AND p.partition_end=v.partition_end
JOIN backfill_jobs j ON j.job_id=v.job_id;

INSERT OR IGNORE INTO backfill_job_partition_refs(
    job_id, source, partition_start, partition_end, created_at
)
SELECT
    r.job_id,
    r.source,
    r.partition_start,
    r.partition_end,
    min(r.created_at)
FROM backfill_verification_rounds r
JOIN backfill_partitions p
  ON p.source=r.source
 AND p.partition_start=r.partition_start
 AND p.partition_end=r.partition_end
JOIN backfill_jobs j ON j.job_id=r.job_id
GROUP BY
    r.job_id, r.source, r.partition_start, r.partition_end;

UPDATE backfill_partitions
SET evidence_config_hash=coalesce(
      (
        SELECT j.config_hash
        FROM backfill_jobs j
        WHERE j.job_id=backfill_partitions.job_id
      ),
      ''
    );

ALTER TABLE backfill_partition_verification_state
    RENAME TO backfill_partition_verification_state_v6;
CREATE TABLE backfill_partition_verification_state (
    source TEXT NOT NULL,
    partition_start TEXT NOT NULL,
    partition_end TEXT NOT NULL,
    rounds_total INTEGER NOT NULL DEFAULT 0
      CHECK (rounds_total >= 0),
    stable_rounds INTEGER NOT NULL DEFAULT 0
      CHECK (stable_rounds >= 0),
    last_probe_hash TEXT NOT NULL DEFAULT '',
    last_new_documents INTEGER NOT NULL DEFAULT 0
      CHECK (last_new_documents >= 0),
    last_new_security_codes INTEGER NOT NULL DEFAULT 0
      CHECK (last_new_security_codes >= 0),
    updated_at TEXT NOT NULL,
    PRIMARY KEY (source, partition_start, partition_end),
    FOREIGN KEY (source, partition_start, partition_end)
      REFERENCES backfill_partitions(
        source, partition_start, partition_end
      )
);
INSERT OR REPLACE INTO backfill_partition_verification_state(
    source, partition_start, partition_end,
    rounds_total, stable_rounds, last_probe_hash,
    last_new_documents, last_new_security_codes, updated_at
)
SELECT
    v.source, v.partition_start, v.partition_end,
    v.rounds_total, v.stable_rounds, v.last_probe_hash,
    v.last_new_documents, v.last_new_security_codes, v.updated_at
FROM backfill_partition_verification_state_v6 v
JOIN backfill_partitions p
  ON p.source=v.source
 AND p.partition_start=v.partition_start
 AND p.partition_end=v.partition_end
JOIN backfill_jobs j ON j.job_id=v.job_id
WHERE trim(p.job_id)<>''
  AND v.job_id=p.job_id
  AND j.config_hash=p.evidence_config_hash;
DROP TABLE backfill_partition_verification_state_v6;

ALTER TABLE backfill_verification_rounds
    RENAME TO backfill_verification_rounds_v6;
CREATE TABLE backfill_verification_rounds (
    source TEXT NOT NULL,
    partition_start TEXT NOT NULL,
    partition_end TEXT NOT NULL,
    round_no INTEGER NOT NULL CHECK (round_no >= 1),
    probe_hash TEXT NOT NULL,
    probe_documents INTEGER NOT NULL
      CHECK (probe_documents >= 0),
    probe_security_codes INTEGER NOT NULL
      CHECK (probe_security_codes >= 0),
    new_documents INTEGER NOT NULL
      CHECK (new_documents >= 0),
    new_security_codes INTEGER NOT NULL
      CHECK (new_security_codes >= 0),
    stable_rounds INTEGER NOT NULL CHECK (stable_rounds >= 0),
    created_at TEXT NOT NULL,
    PRIMARY KEY (
      source, partition_start, partition_end, round_no
    ),
    FOREIGN KEY (source, partition_start, partition_end)
      REFERENCES backfill_partitions(
        source, partition_start, partition_end
      )
);
INSERT OR REPLACE INTO backfill_verification_rounds(
    source, partition_start, partition_end, round_no,
    probe_hash, probe_documents, probe_security_codes,
    new_documents, new_security_codes, stable_rounds, created_at
)
SELECT
    r.source, r.partition_start, r.partition_end, r.round_no,
    r.probe_hash, r.probe_documents, r.probe_security_codes,
    r.new_documents, r.new_security_codes, r.stable_rounds,
    r.created_at
FROM backfill_verification_rounds_v6 r
JOIN backfill_partitions p
  ON p.source=r.source
 AND p.partition_start=r.partition_start
 AND p.partition_end=r.partition_end
JOIN backfill_jobs j ON j.job_id=r.job_id
WHERE trim(p.job_id)<>''
  AND r.job_id=p.job_id
  AND j.config_hash=p.evidence_config_hash
ORDER BY r.round_no;
DROP TABLE backfill_verification_rounds_v6;
CREATE INDEX IF NOT EXISTS idx_backfill_verification_partition
    ON backfill_verification_rounds(
      source, partition_start, partition_end, round_no
    );

UPDATE backfill_partitions
SET job_id=''
WHERE trim(job_id)<>'';

INSERT INTO document_security_links(
    document_id, ts_code, name, provenance, created_at, updated_at
)
SELECT
    d.id,
    upper(trim(json_extract(link.value, '$.ts_code'))),
    coalesce(trim(json_extract(link.value, '$.name')), ''),
    'legacy_metadata',
    d.first_seen_at,
    d.first_seen_at
FROM documents d
JOIN json_each(
  CASE WHEN json_valid(d.metadata_json)
       THEN d.metadata_json ELSE '{}' END,
  '$.security_links'
) AS link
WHERE json_type(link.value)='object'
  AND trim(coalesce(json_extract(link.value, '$.ts_code'), ''))<>''
ON CONFLICT(document_id, ts_code) DO UPDATE SET
    name=CASE
      WHEN document_security_links.name='' THEN excluded.name
      WHEN excluded.name='' THEN document_security_links.name
      WHEN excluded.name < document_security_links.name
      THEN excluded.name
      ELSE document_security_links.name
    END,
    provenance=CASE
      WHEN excluded.provenance < document_security_links.provenance
      THEN excluded.provenance
      ELSE document_security_links.provenance
    END,
    created_at=min(document_security_links.created_at, excluded.created_at),
    updated_at=max(document_security_links.updated_at, excluded.updated_at);

INSERT INTO document_security_links(
    document_id, ts_code, name, provenance, created_at, updated_at
)
SELECT
    d.id,
    upper(trim(code.value)),
    '',
    'legacy_metadata',
    d.first_seen_at,
    d.first_seen_at
FROM documents d
JOIN json_each(
  CASE WHEN json_valid(d.metadata_json)
       THEN d.metadata_json ELSE '{}' END,
  '$.security_codes'
) AS code
WHERE trim(coalesce(code.value, ''))<>''
ON CONFLICT(document_id, ts_code) DO UPDATE SET
    provenance=CASE
      WHEN excluded.provenance < document_security_links.provenance
      THEN excluded.provenance
      ELSE document_security_links.provenance
    END,
    created_at=min(document_security_links.created_at, excluded.created_at),
    updated_at=max(document_security_links.updated_at, excluded.updated_at);

INSERT INTO document_security_links(
    document_id, ts_code, name, provenance, created_at, updated_at
)
SELECT
    d.id,
    upper(trim(json_extract(d.metadata_json, '$.ts_code'))),
    coalesce(trim(json_extract(d.metadata_json, '$.name')), ''),
    'legacy_metadata',
    d.first_seen_at,
    d.first_seen_at
FROM documents d
WHERE json_valid(d.metadata_json)
  AND trim(coalesce(json_extract(d.metadata_json, '$.ts_code'), ''))<>''
ON CONFLICT(document_id, ts_code) DO UPDATE SET
    name=CASE
      WHEN document_security_links.name='' THEN excluded.name
      WHEN excluded.name='' THEN document_security_links.name
      WHEN excluded.name < document_security_links.name
      THEN excluded.name
      ELSE document_security_links.name
    END,
    provenance=CASE
      WHEN excluded.provenance < document_security_links.provenance
      THEN excluded.provenance
      ELSE document_security_links.provenance
    END,
    created_at=min(document_security_links.created_at, excluded.created_at),
    updated_at=max(document_security_links.updated_at, excluded.updated_at);

INSERT INTO announcement_security_catalog(
    source, ts_code, name, provenance,
    first_seen_at, last_seen_at, observations
)
SELECT
    d.source,
    upper(trim(l.ts_code)),
    coalesce(min(nullif(trim(l.name), '')), ''),
    coalesce(min(nullif(trim(l.provenance), '')), 'document_link'),
    min(l.created_at),
    max(l.updated_at),
    count(*)
FROM document_security_links l
JOIN documents d ON d.id=l.document_id
WHERE d.source='tushare_announcement'
  AND trim(l.ts_code)<>''
GROUP BY d.source, upper(trim(l.ts_code))
ON CONFLICT(source, ts_code) DO UPDATE SET
    name=CASE
      WHEN announcement_security_catalog.name='' THEN excluded.name
      WHEN excluded.name='' THEN announcement_security_catalog.name
      WHEN excluded.name < announcement_security_catalog.name
      THEN excluded.name
      ELSE announcement_security_catalog.name
    END,
    provenance=CASE
      WHEN excluded.provenance
           < announcement_security_catalog.provenance
      THEN excluded.provenance
      ELSE announcement_security_catalog.provenance
    END,
    first_seen_at=min(
      announcement_security_catalog.first_seen_at,
      excluded.first_seen_at
    ),
    last_seen_at=max(
      announcement_security_catalog.last_seen_at,
      excluded.last_seen_at
    ),
    observations=max(
      announcement_security_catalog.observations,
      excluded.observations
    );

UPDATE documents
SET link_revision=(
      SELECT COUNT(*)
      FROM document_security_links links
      WHERE links.document_id=documents.id
    ),
    extracted_link_revision=0,
    status=CASE
      WHEN status IN ('processed', 'no_event')
       AND EXISTS (
         SELECT 1
         FROM document_security_links links
         WHERE links.document_id=documents.id
       )
      THEN 'collected'
      ELSE status
    END;
"""

MIGRATION_V8 = """
ALTER TABLE backfill_jobs
    ADD COLUMN exact_config_hash TEXT NOT NULL DEFAULT '';
ALTER TABLE backfill_jobs
    ADD COLUMN compatibility_hash TEXT NOT NULL DEFAULT '';
ALTER TABLE backfill_jobs
    ADD COLUMN config_json TEXT NOT NULL DEFAULT '{}'
    CHECK (json_valid(config_json));
ALTER TABLE backfill_jobs
    ADD COLUMN evidence_status TEXT NOT NULL DEFAULT 'current'
    CHECK (
      evidence_status IN ('current', 'needs_revalidation')
    );

ALTER TABLE backfill_job_partition_refs
    ADD COLUMN evidence_status TEXT NOT NULL DEFAULT 'exact'
    CHECK (
      evidence_status IN (
        'exact', 'compatible_limit_upgrade', 'needs_revalidation'
      )
    );
ALTER TABLE backfill_job_partition_refs
    ADD COLUMN association_provenance TEXT NOT NULL
    DEFAULT 'legacy_explicit';

ALTER TABLE backfill_partitions
    ADD COLUMN evidence_compatibility_hash TEXT NOT NULL DEFAULT '';
ALTER TABLE backfill_partitions
    ADD COLUMN evidence_request_limit INTEGER NOT NULL DEFAULT 0
    CHECK (evidence_request_limit >= 0);
ALTER TABLE backfill_partitions
    ADD COLUMN catalog_revision INTEGER NOT NULL DEFAULT 0
    CHECK (catalog_revision >= 0);
ALTER TABLE backfill_partitions
    ADD COLUMN catalog_hash TEXT NOT NULL DEFAULT '';
ALTER TABLE backfill_partitions
    ADD COLUMN completion_basis TEXT NOT NULL DEFAULT '';

CREATE TABLE IF NOT EXISTS announcement_catalog_state (
    source TEXT PRIMARY KEY,
    revision INTEGER NOT NULL DEFAULT 0 CHECK (revision >= 0),
    content_hash TEXT NOT NULL DEFAULT '',
    security_count INTEGER NOT NULL DEFAULT 0
      CHECK (security_count >= 0),
    updated_at TEXT NOT NULL
);

UPDATE backfill_jobs
SET exact_config_hash=config_hash,
    compatibility_hash=config_hash,
    config_json='{}';

UPDATE backfill_partitions
SET evidence_request_limit=CASE
      WHEN evidence_config_hash<>'' THEN request_limit ELSE 0 END,
    evidence_compatibility_hash=evidence_config_hash;

INSERT OR IGNORE INTO backfill_job_partition_refs(
    job_id, source, partition_start, partition_end,
    created_at, evidence_status, association_provenance
)
SELECT
    j.job_id,
    p.source,
    p.partition_start,
    p.partition_end,
    p.updated_at,
    'needs_revalidation',
    'range_inferred'
FROM backfill_jobs j
JOIN backfill_partitions p
  ON p.source=j.source
 AND p.partition_start>=j.start_date
 AND p.partition_end<=j.end_date;

UPDATE backfill_job_partition_refs
SET evidence_status=CASE
      WHEN association_provenance='range_inferred'
      THEN 'needs_revalidation'
      WHEN EXISTS (
        SELECT 1
        FROM backfill_jobs j
        JOIN backfill_partitions p
          ON p.source=backfill_job_partition_refs.source
         AND p.partition_start=
             backfill_job_partition_refs.partition_start
         AND p.partition_end=
             backfill_job_partition_refs.partition_end
        WHERE j.job_id=backfill_job_partition_refs.job_id
          AND j.exact_config_hash=p.evidence_config_hash
      )
      THEN 'exact'
      WHEN EXISTS (
        SELECT 1
        FROM backfill_jobs j
        JOIN backfill_partitions p
          ON p.source=backfill_job_partition_refs.source
         AND p.partition_start=
             backfill_job_partition_refs.partition_start
         AND p.partition_end=
             backfill_job_partition_refs.partition_end
        WHERE j.job_id=backfill_job_partition_refs.job_id
          AND j.compatibility_hash=
              p.evidence_compatibility_hash
          AND j.request_limit>p.evidence_request_limit
      )
      THEN 'compatible_limit_upgrade'
      ELSE 'needs_revalidation'
    END;

UPDATE backfill_jobs
SET evidence_status='needs_revalidation',
    status='partial'
WHERE EXISTS (
    SELECT 1
    FROM backfill_job_partition_refs r
    WHERE r.job_id=backfill_jobs.job_id
      AND r.evidence_status='needs_revalidation'
);

INSERT INTO announcement_catalog_state(
    source, revision, content_hash, security_count, updated_at
)
SELECT
    source,
    count(*),
    '',
    count(*),
    max(last_seen_at)
FROM announcement_security_catalog
GROUP BY source;

CREATE INDEX IF NOT EXISTS idx_backfill_refs_evidence_status
    ON backfill_job_partition_refs(
      job_id, evidence_status, source, partition_start, partition_end
    );
"""

MIGRATION_V9 = """
ALTER TABLE backfill_jobs
    ADD COLUMN generation INTEGER NOT NULL DEFAULT 0
    CHECK (generation >= 0);

UPDATE backfill_partitions
SET completion_basis='split_children'
WHERE status='complete'
  AND trim(completion_basis)=''
  AND partition_start<partition_end
  AND (
    error='split_complete'
    OR (
      EXISTS (
        SELECT 1
        FROM backfill_partitions left_child
        WHERE left_child.source=backfill_partitions.source
          AND left_child.partition_start=
              backfill_partitions.partition_start
          AND left_child.partition_end=date(
                backfill_partitions.partition_start,
                '+' || CAST(
                  (
                    julianday(backfill_partitions.partition_end)
                    - julianday(backfill_partitions.partition_start)
                  ) / 2 AS INTEGER
                ) || ' days'
              )
          AND left_child.status='complete'
      )
      AND EXISTS (
        SELECT 1
        FROM backfill_partitions right_child
        WHERE right_child.source=backfill_partitions.source
          AND right_child.partition_start=date(
                backfill_partitions.partition_start,
                '+' || (
                  CAST(
                    (
                      julianday(backfill_partitions.partition_end)
                      - julianday(backfill_partitions.partition_start)
                    ) / 2 AS INTEGER
                  ) + 1
                ) || ' days'
              )
          AND right_child.partition_end=
              backfill_partitions.partition_end
          AND right_child.status='complete'
      )
    )
  );

UPDATE backfill_partitions
SET catalog_hash=coalesce(
      (
        SELECT child.catalog_hash
        FROM backfill_partitions child
        WHERE child.source=backfill_partitions.source
          AND child.partition_start>=
              backfill_partitions.partition_start
          AND child.partition_end<=
              backfill_partitions.partition_end
          AND (
            child.partition_start>
              backfill_partitions.partition_start
            OR child.partition_end<
              backfill_partitions.partition_end
          )
          AND child.catalog_revision>=
              backfill_partitions.catalog_revision
          AND child.catalog_revision>0
          AND trim(child.catalog_hash)<>''
        ORDER BY
          child.catalog_revision DESC,
          child.partition_start,
          child.partition_end
        LIMIT 1
      ),
      catalog_hash
    ),
    catalog_revision=max(
      catalog_revision,
      coalesce(
        (
          SELECT max(child.catalog_revision)
          FROM backfill_partitions child
          WHERE child.source=backfill_partitions.source
            AND child.partition_start>=
                backfill_partitions.partition_start
            AND child.partition_end<=
                backfill_partitions.partition_end
            AND (
              child.partition_start>
                backfill_partitions.partition_start
              OR child.partition_end<
                backfill_partitions.partition_end
            )
            AND child.catalog_revision>0
            AND trim(child.catalog_hash)<>''
        ),
        0
      )
    )
WHERE completion_basis='split_children';

CREATE INDEX IF NOT EXISTS idx_backfill_partitions_tree
    ON backfill_partitions(
      source, partition_start, partition_end,
      completion_basis, status
    );
"""

MIGRATION_V10 = """
UPDATE backfill_partitions
SET status='failed_overflow',
    error='split_descendant_catalog_growth',
    completion_strategy_version=0,
    completion_basis='split_children_revalidation',
    catalog_revision=coalesce(
      (
        SELECT stale_leaf.catalog_revision
        FROM backfill_partitions stale_leaf
        WHERE stale_leaf.source=backfill_partitions.source
          AND stale_leaf.partition_start>=
              backfill_partitions.partition_start
          AND stale_leaf.partition_end<=
              backfill_partitions.partition_end
          AND (
            stale_leaf.partition_start>
              backfill_partitions.partition_start
            OR stale_leaf.partition_end<
              backfill_partitions.partition_end
          )
          AND stale_leaf.status='failed_overflow'
          AND stale_leaf.error='catalog_growth_revalidation'
          AND stale_leaf.probe_manifest_version>=1
        ORDER BY
          stale_leaf.catalog_revision,
          stale_leaf.partition_start,
          stale_leaf.partition_end
        LIMIT 1
      ),
      catalog_revision
    ),
    catalog_hash=coalesce(
      (
        SELECT stale_leaf.catalog_hash
        FROM backfill_partitions stale_leaf
        WHERE stale_leaf.source=backfill_partitions.source
          AND stale_leaf.partition_start>=
              backfill_partitions.partition_start
          AND stale_leaf.partition_end<=
              backfill_partitions.partition_end
          AND (
            stale_leaf.partition_start>
              backfill_partitions.partition_start
            OR stale_leaf.partition_end<
              backfill_partitions.partition_end
          )
          AND stale_leaf.status='failed_overflow'
          AND stale_leaf.error='catalog_growth_revalidation'
          AND stale_leaf.probe_manifest_version>=1
        ORDER BY
          stale_leaf.catalog_revision,
          stale_leaf.partition_start,
          stale_leaf.partition_end
        LIMIT 1
      ),
      catalog_hash
    ),
    updated_at=CURRENT_TIMESTAMP
WHERE (
    completion_basis IN (
      'split_children',
      'split_children_revalidation'
    )
    OR error IN (
      'split_complete',
      'split_descendant_catalog_growth'
    )
  )
  AND EXISTS (
    SELECT 1
    FROM backfill_partitions stale_leaf
    WHERE stale_leaf.source=backfill_partitions.source
      AND stale_leaf.partition_start>=
          backfill_partitions.partition_start
      AND stale_leaf.partition_end<=
          backfill_partitions.partition_end
      AND (
        stale_leaf.partition_start>
          backfill_partitions.partition_start
        OR stale_leaf.partition_end<
          backfill_partitions.partition_end
      )
      AND stale_leaf.status='failed_overflow'
      AND stale_leaf.error='catalog_growth_revalidation'
      AND stale_leaf.probe_manifest_version>=1
  );

UPDATE backfill_partition_verification_state
SET stable_rounds=0,
    last_probe_hash='',
    last_new_documents=0,
    last_new_security_codes=0,
    updated_at=CURRENT_TIMESTAMP
WHERE EXISTS (
  SELECT 1
  FROM backfill_partitions stale_leaf
  WHERE stale_leaf.source=
        backfill_partition_verification_state.source
    AND stale_leaf.partition_start=
        backfill_partition_verification_state.partition_start
    AND stale_leaf.partition_end=
        backfill_partition_verification_state.partition_end
    AND stale_leaf.status='failed_overflow'
    AND stale_leaf.error='catalog_growth_revalidation'
    AND stale_leaf.probe_manifest_version>=1
);

UPDATE backfill_job_partition_refs
SET evidence_status='needs_revalidation'
WHERE EXISTS (
  SELECT 1
  FROM backfill_partitions invalid_partition
  WHERE invalid_partition.source=
        backfill_job_partition_refs.source
    AND invalid_partition.partition_start=
        backfill_job_partition_refs.partition_start
    AND invalid_partition.partition_end=
        backfill_job_partition_refs.partition_end
    AND invalid_partition.status='failed_overflow'
    AND invalid_partition.error IN (
      'catalog_growth_revalidation',
      'split_descendant_catalog_growth'
    )
);

UPDATE backfill_jobs
SET status='partial',
    evidence_status='needs_revalidation',
    generation=generation+1,
    updated_at=CURRENT_TIMESTAMP
WHERE EXISTS (
  SELECT 1
  FROM backfill_job_partition_refs ref
  JOIN backfill_partitions invalid_partition
    ON invalid_partition.source=ref.source
   AND invalid_partition.partition_start=ref.partition_start
   AND invalid_partition.partition_end=ref.partition_end
  WHERE ref.job_id=backfill_jobs.job_id
    AND invalid_partition.status='failed_overflow'
    AND invalid_partition.error IN (
      'catalog_growth_revalidation',
      'split_descendant_catalog_growth'
    )
);
"""

MIGRATION_V11 = """
UPDATE backfill_partitions
SET status='failed_overflow',
    error='split_descendant_revalidation',
    completion_strategy_version=0,
    completion_basis='split_children_revalidation',
    updated_at=CURRENT_TIMESTAMP
WHERE status='complete'
  AND completion_basis='split_children'
  AND EXISTS (
    SELECT 1
    FROM backfill_partitions invalid_descendant
    WHERE invalid_descendant.source=backfill_partitions.source
      AND invalid_descendant.partition_start>=
          backfill_partitions.partition_start
      AND invalid_descendant.partition_end<=
          backfill_partitions.partition_end
      AND (
        invalid_descendant.partition_start>
          backfill_partitions.partition_start
        OR invalid_descendant.partition_end<
          backfill_partitions.partition_end
      )
      AND invalid_descendant.probe_manifest_version>=1
      AND (
        invalid_descendant.status<>'complete'
        OR invalid_descendant.completion_strategy_version<>3
      )
  );

UPDATE backfill_partition_verification_state
SET stable_rounds=0,
    last_probe_hash='',
    last_new_documents=0,
    last_new_security_codes=0,
    updated_at=CURRENT_TIMESTAMP
WHERE EXISTS (
    SELECT 1
    FROM backfill_partitions invalid_descendant
    JOIN backfill_partitions reopened_ancestor
      ON reopened_ancestor.source=invalid_descendant.source
     AND reopened_ancestor.partition_start<=
         invalid_descendant.partition_start
     AND reopened_ancestor.partition_end>=
         invalid_descendant.partition_end
     AND (
       reopened_ancestor.partition_start<
         invalid_descendant.partition_start
       OR reopened_ancestor.partition_end>
         invalid_descendant.partition_end
     )
     AND reopened_ancestor.status='failed_overflow'
     AND reopened_ancestor.completion_basis=
         'split_children_revalidation'
    WHERE invalid_descendant.source=
          backfill_partition_verification_state.source
      AND invalid_descendant.partition_start=
          backfill_partition_verification_state.partition_start
      AND invalid_descendant.partition_end=
          backfill_partition_verification_state.partition_end
      AND invalid_descendant.probe_manifest_version>=1
      AND (
        invalid_descendant.status<>'complete'
        OR invalid_descendant.completion_strategy_version<>3
      )
);

UPDATE backfill_job_partition_refs
SET evidence_status='needs_revalidation'
WHERE EXISTS (
    SELECT 1
    FROM backfill_partitions invalid_descendant
    JOIN backfill_partitions reopened_ancestor
      ON reopened_ancestor.source=invalid_descendant.source
     AND reopened_ancestor.partition_start<=
         invalid_descendant.partition_start
     AND reopened_ancestor.partition_end>=
         invalid_descendant.partition_end
     AND (
       reopened_ancestor.partition_start<
         invalid_descendant.partition_start
       OR reopened_ancestor.partition_end>
         invalid_descendant.partition_end
     )
     AND reopened_ancestor.status='failed_overflow'
     AND reopened_ancestor.completion_basis=
         'split_children_revalidation'
    WHERE invalid_descendant.probe_manifest_version>=1
      AND (
        invalid_descendant.status<>'complete'
        OR invalid_descendant.completion_strategy_version<>3
      )
      AND backfill_job_partition_refs.source=
          invalid_descendant.source
      AND (
        (
          backfill_job_partition_refs.partition_start=
            invalid_descendant.partition_start
          AND backfill_job_partition_refs.partition_end=
            invalid_descendant.partition_end
        )
        OR (
          backfill_job_partition_refs.partition_start=
            reopened_ancestor.partition_start
          AND backfill_job_partition_refs.partition_end=
            reopened_ancestor.partition_end
        )
      )
);

UPDATE backfill_jobs
SET status='partial',
    evidence_status='needs_revalidation',
    generation=generation+1,
    updated_at=CURRENT_TIMESTAMP
WHERE evidence_status<>'needs_revalidation'
  AND EXISTS (
    SELECT 1
    FROM backfill_job_partition_refs ref
    WHERE ref.job_id=backfill_jobs.job_id
      AND ref.evidence_status='needs_revalidation'
);
"""

MIGRATION_V12 = """
UPDATE documents
SET raw_path=''
WHERE source='tushare_announcement'
  AND json_extract(metadata_json, '$.content_scope')='title_metadata';
"""

MIGRATION_V13 = """
CREATE TABLE IF NOT EXISTS source_audit_runs (
    run_id TEXT PRIMARY KEY,
    as_of_date TEXT NOT NULL,
    dataset_scope TEXT NOT NULL,
    primary_source TEXT NOT NULL,
    secondary_source TEXT NOT NULL,
    status TEXT NOT NULL
      CHECK (status IN ('running', 'success', 'degraded', 'failed')),
    supplement_enabled INTEGER NOT NULL DEFAULT 0
      CHECK (supplement_enabled IN (0, 1)),
    metrics_json TEXT NOT NULL DEFAULT '{}'
      CHECK (json_valid(metrics_json)),
    started_at TEXT NOT NULL,
    finished_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_source_audit_runs_date
    ON source_audit_runs(as_of_date, started_at);

CREATE TABLE IF NOT EXISTS source_audit_items (
    run_id TEXT NOT NULL REFERENCES source_audit_runs(run_id)
      ON DELETE CASCADE,
    dataset TEXT NOT NULL,
    item_key TEXT NOT NULL,
    comparison_status TEXT NOT NULL
      CHECK (comparison_status IN (
        'matched', 'mismatch', 'primary_only', 'secondary_only',
        'supplemented'
      )),
    primary_id TEXT NOT NULL DEFAULT '',
    secondary_id TEXT NOT NULL DEFAULT '',
    detail_json TEXT NOT NULL DEFAULT '{}'
      CHECK (json_valid(detail_json)),
    PRIMARY KEY (run_id, dataset, item_key)
);

CREATE INDEX IF NOT EXISTS idx_source_audit_items_status
    ON source_audit_items(dataset, comparison_status);
"""

MIGRATION_V14 = """
CREATE TABLE IF NOT EXISTS artifact_worker_jobs (
    job_id TEXT PRIMARY KEY,
    worker_id TEXT NOT NULL,
    stage TEXT NOT NULL CHECK (stage IN ('download', 'parse')),
    status TEXT NOT NULL
      CHECK (status IN (
        'leased', 'importing', 'imported', 'partial', 'failed', 'expired'
      )),
    created_at TEXT NOT NULL,
    lease_until TEXT NOT NULL,
    finished_at TEXT,
    manifest_hash TEXT NOT NULL DEFAULT '',
    result_hash TEXT NOT NULL DEFAULT '',
    counts_json TEXT NOT NULL DEFAULT '{}'
      CHECK (json_valid(counts_json))
);

CREATE INDEX IF NOT EXISTS idx_artifact_worker_jobs_active
    ON artifact_worker_jobs(stage, status, lease_until, created_at);

CREATE TABLE IF NOT EXISTS artifact_worker_items (
    job_id TEXT NOT NULL REFERENCES artifact_worker_jobs(job_id)
      ON DELETE CASCADE,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    document_id INTEGER NOT NULL REFERENCES documents(id),
    input_hash TEXT NOT NULL,
    status TEXT NOT NULL
      CHECK (status IN (
        'leased', 'succeeded', 'failed_retryable',
        'failed_terminal', 'reused'
      )),
    error TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL,
    PRIMARY KEY (job_id, document_id),
    UNIQUE (job_id, ordinal)
);

CREATE INDEX IF NOT EXISTS idx_artifact_worker_items_document
    ON artifact_worker_items(document_id, status, job_id);
"""

MIGRATION_V15 = """
CREATE TABLE IF NOT EXISTS semantic_run_replacements (
    repair_id TEXT NOT NULL,
    document_id INTEGER NOT NULL REFERENCES documents(id),
    superseded_run_id TEXT NOT NULL REFERENCES semantic_runs(run_id),
    replacement_run_id TEXT NOT NULL REFERENCES semantic_runs(run_id),
    reason TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active', 'rolled_back')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (repair_id, superseded_run_id),
    CHECK (superseded_run_id <> replacement_run_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_semantic_run_replacements_active
    ON semantic_run_replacements(superseded_run_id)
    WHERE status='active';

CREATE INDEX IF NOT EXISTS idx_semantic_run_replacements_replacement
    ON semantic_run_replacements(replacement_run_id, status);
"""

MIGRATION_V16 = """
CREATE TABLE IF NOT EXISTS semantic_contract_profiles (
    profile_id TEXT PRIMARY KEY,
    profile_hash TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN (
      'draft', 'canary', 'preaccepted', 'shadow', 'active',
      'paused', 'retired', 'rejected'
    )),
    error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS semantic_executor_bindings (
    binding_id TEXT PRIMARY KEY,
    profile_id TEXT NOT NULL REFERENCES semantic_contract_profiles(profile_id),
    executor_mode TEXT NOT NULL CHECK (executor_mode IN ('api', 'coding_plan')),
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    client_version TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN (
      'untested', 'compatible', 'shadow', 'production_qualified', 'suspended'
    )),
    error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(profile_id, executor_mode, provider, model, client_version)
);

CREATE TABLE IF NOT EXISTS semantic_tasks (
    semantic_task_id TEXT PRIMARY KEY,
    document_id INTEGER NOT NULL REFERENCES documents(id),
    profile_id TEXT NOT NULL REFERENCES semantic_contract_profiles(profile_id),
    artifact_hash TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN (
      'prepared', 'assigned', 'running', 'retry_wait', 'produced',
      'validating', 'retrying_event', 'accepted', 'quarantined', 'abandoned'
    )),
    error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(document_id, profile_id, artifact_hash, input_hash)
);

CREATE TABLE IF NOT EXISTS semantic_execution_jobs (
    execution_job_id TEXT PRIMARY KEY,
    semantic_task_id TEXT NOT NULL REFERENCES semantic_tasks(semantic_task_id),
    binding_id TEXT NOT NULL REFERENCES semantic_executor_bindings(binding_id),
    status TEXT NOT NULL CHECK (status IN (
      'assigned', 'running', 'retry_wait', 'produced', 'validating',
      'retrying_event', 'accepted', 'quarantined', 'abandoned'
    )),
    output_hash TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    finished_at TEXT,
    UNIQUE(semantic_task_id, binding_id)
);

CREATE INDEX IF NOT EXISTS idx_semantic_tasks_status
    ON semantic_tasks(profile_id, status, document_id);
CREATE INDEX IF NOT EXISTS idx_semantic_execution_jobs_status
    ON semantic_execution_jobs(binding_id, status, created_at);
"""

MIGRATIONS = {
    1: MIGRATION_V1,
    2: MIGRATION_V2,
    3: MIGRATION_V3,
    4: MIGRATION_V4,
    5: MIGRATION_V5,
    6: MIGRATION_V6,
    7: MIGRATION_V7,
    8: MIGRATION_V8,
    9: MIGRATION_V9,
    10: MIGRATION_V10,
    11: MIGRATION_V11,
    12: MIGRATION_V12,
    13: MIGRATION_V13,
    14: MIGRATION_V14,
    15: MIGRATION_V15,
    16: MIGRATION_V16,
}
