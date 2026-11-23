-- Shared by the source DB (SQLite stand-in for production PostgreSQL) and the warehouse (Snowflake / DuckDB).
CREATE TABLE IF NOT EXISTS agents (
    agent_id     INTEGER,
    agent_name   VARCHAR,
    team         VARCHAR,
    updated_at   TIMESTAMP
);

CREATE TABLE IF NOT EXISTS leads (
    lead_id      INTEGER,
    agent_id     INTEGER,
    source       VARCHAR,
    neighborhood VARCHAR,
    bedrooms     INTEGER,
    budget       INTEGER,
    status       VARCHAR,
    funnel_step  INTEGER,
    monthly_rent NUMERIC(10, 2),
    commission   NUMERIC(10, 2),
    created_at   TIMESTAMP,
    updated_at   TIMESTAMP
);

CREATE TABLE IF NOT EXISTS calls (
    call_id      INTEGER,
    lead_id      INTEGER,
    agent_id     INTEGER,
    channel      VARCHAR,
    outcome      VARCHAR,
    duration_sec INTEGER,
    called_at    TIMESTAMP,
    updated_at   TIMESTAMP
);
