-- Extensions required by the AeroTwin schema (Doc 04).
-- Executed once, on first initialisation of the postgres data volume.

CREATE EXTENSION IF NOT EXISTS timescaledb;   -- hypertables + continuous aggregates
CREATE EXTENSION IF NOT EXISTS pg_trgm;       -- trigram search on engine references
CREATE EXTENSION IF NOT EXISTS citext;        -- case-insensitive user emails

-- Verify TimescaleDB is actually available; failing loudly here is far better
-- than discovering it during the first hypertable migration.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'timescaledb') THEN
        RAISE EXCEPTION 'TimescaleDB extension is required but not installed';
    END IF;
    RAISE NOTICE 'AeroTwin: extensions ready.';
END
$$;
