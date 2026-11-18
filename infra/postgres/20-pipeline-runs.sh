#!/bin/bash
# Suivi des TRAITEMENTS (flows Prefect) pour le dashboard Grafana.
# Exécuté automatiquement au premier démarrage de PostgreSQL. Idempotent : peut aussi être relancé à la main
# sur une base existante (voir docs/SUPERVISION.md).
# Rôle dédié pipeline_writer : il ne peut QU'ajouter et lire des lignes dans tracing.pipeline_runs.
set -euo pipefail
psql -v ON_ERROR_STOP=1 -U postgres -d velos <<SQL
DO \$\$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'pipeline_writer') THEN
    CREATE ROLE pipeline_writer LOGIN PASSWORD '${PIPELINE_WRITER_PASSWORD}';
  ELSE
    ALTER ROLE pipeline_writer PASSWORD '${PIPELINE_WRITER_PASSWORD}';
  END IF;
END
\$\$;

CREATE TABLE IF NOT EXISTS tracing.pipeline_runs (
    run_id         uuid        PRIMARY KEY,
    flow_name      text        NOT NULL,
    lot_id         text        NOT NULL,
    started_at     timestamptz NOT NULL,
    finished_at    timestamptz NOT NULL,
    duration_s     double precision NOT NULL,
    status         text        NOT NULL CHECK (status IN ('succeeded', 'rejected', 'failed', 'already_processed')),
    model_version  integer,
    verdict        text,
    error          text
);
CREATE INDEX IF NOT EXISTS pipeline_runs_started_at ON tracing.pipeline_runs (started_at);

GRANT USAGE ON SCHEMA tracing TO pipeline_writer;
GRANT SELECT, INSERT ON tracing.pipeline_runs TO pipeline_writer;
GRANT SELECT ON tracing.pipeline_runs TO grafana_reader;
SQL
