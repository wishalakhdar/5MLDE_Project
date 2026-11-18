"""Journal des traitements du pipeline dans PostgreSQL (tracing.pipeline_runs), lu par Grafana.

« Au mieux » : si la base est injoignable ou si le mot de passe n'est pas fourni, le traitement continue et
un avertissement est journalisé. Le suivi ne doit jamais faire échouer (ni réussir) un entraînement.
Connexion avec le rôle dédié `pipeline_writer` (INSERT/SELECT sur cette seule table).
Mot de passe : variable PIPELINE_WRITER_PASSWORD (lancer avec `uv run --env-file .env ...`).
"""
from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime

log = logging.getLogger("velos.run_log")
STATUSES = ("succeeded", "rejected", "failed", "already_processed")


def make_entry(flow_name: str, lot_id: str, started_at: datetime, finished_at: datetime, status: str,
               model_version: int | None = None, verdict: str | None = None, error: str | None = None) -> dict:
    if status not in STATUSES:
        raise ValueError(f"statut inconnu : {status}")
    return {"run_id": uuid.uuid4(), "flow_name": flow_name, "lot_id": lot_id, "started_at": started_at,
            "finished_at": finished_at, "duration_s": (finished_at - started_at).total_seconds(),
            "status": status, "model_version": model_version, "verdict": verdict,
            "error": (error or None) and error[:500]}


def record_pipeline_run(entry: dict, environ=os.environ, connect=None) -> bool:
    """Insère la ligne. Retourne True si elle est enregistrée, False sinon (sans jamais lever)."""
    password = environ.get("PIPELINE_WRITER_PASSWORD")
    if not password:
        log.warning("Traitement non journalisé dans PostgreSQL : PIPELINE_WRITER_PASSWORD absent "
                    "(lancer avec `uv run --env-file .env ...`)")
        return False
    try:
        if connect is None:
            import psycopg
            connect = psycopg.connect
        with connect(host=environ.get("VELOS_DB_HOST", "127.0.0.1"), port=environ.get("POSTGRES_PORT", "5432"),
                     dbname="velos", user="pipeline_writer", password=password, connect_timeout=3) as conn:
            conn.execute(
                "INSERT INTO tracing.pipeline_runs (run_id, flow_name, lot_id, started_at, finished_at, duration_s,"
                " status, model_version, verdict, error) VALUES (%(run_id)s, %(flow_name)s, %(lot_id)s,"
                " %(started_at)s, %(finished_at)s, %(duration_s)s, %(status)s, %(model_version)s, %(verdict)s,"
                " %(error)s)", entry)
        return True
    except Exception as exc:
        log.warning("Traitement non journalisé dans PostgreSQL : %s: %s", type(exc).__name__,
                    str(exc).splitlines()[0] if str(exc) else "")
        return False
