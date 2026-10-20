CREATE TABLE IF NOT EXISTS snapshots (
    id BIGSERIAL PRIMARY KEY,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    seconds REAL,
    n_models INTEGER,
    errors JSONB NOT NULL DEFAULT '[]'::jsonb,
    bundle JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS daily (
    date DATE NOT NULL,
    permaslug TEXT NOT NULL,
    variant TEXT NOT NULL,
    author TEXT NOT NULL,
    prompt BIGINT NOT NULL,
    completion BIGINT NOT NULL,
    cached BIGINT NOT NULL,
    reasoning BIGINT NOT NULL,
    requests BIGINT NOT NULL,
    est_usd DOUBLE PRECISION NOT NULL,
    spend_usd DOUBLE PRECISION NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (date, permaslug, variant)
);

CREATE TABLE IF NOT EXISTS runs (
    id BIGSERIAL PRIMARY KEY,
    started_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ,
    ok BOOLEAN,
    seconds REAL,
    n_models INTEGER,
    n_rows INTEGER,
    build_ms REAL,
    error TEXT
);
