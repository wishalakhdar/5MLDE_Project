"""Stockage PostgreSQL des traces.

- Tests unitaires : un faux pool de connexions enregistre les requêtes SQL (pas besoin de base).
- Test d'intégration : vraie base Docker, lancé seulement si VELOS_PG_TESTS=1
  (voir docs/API.md : uv run --env-file .env pytest tests/test_postgres_store.py).
"""
import json
import os
import uuid
from contextlib import contextmanager

import pytest

from velos.api.persistence import (DuplicateFeedback, PredictionTrace, RequestTrace, StoreUnavailable,
                                  UnknownPrediction)
from velos.api.postgres_store import PostgresTraceStore, conninfo, connection_settings
from velos.errors import ConfigError


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def executemany(self, sql, rows):
        self.conn.statements.extend((sql, row) for row in rows)


class FakeConnection:
    def __init__(self, fail=None):
        self.statements = []
        self.fail = fail

    def execute(self, sql, params=None):
        if self.fail:
            raise self.fail
        self.statements.append((sql, params))

    def cursor(self):
        return FakeCursor(self)


class FakePool:
    """Imite psycopg_pool.ConnectionPool : prête une connexion dans un bloc `with`."""

    def __init__(self, fail=None):
        self.conn = FakeConnection(fail)
        self.closed = False

    @contextmanager
    def connection(self, timeout=None):
        yield self.conn

    def close(self):
        self.closed = True


class ForeignKeyError(Exception):
    sqlstate = "23503"


class UniqueViolationError(Exception):
    sqlstate = "23505"


def trace(version=1):
    return RequestTrace(uuid.uuid4(), "/predict", 200, 4.2, version, None)


def test_reglages_lus_depuis_config_et_environnement():
    cfg = {"api": {"postgres": {"database": "velos", "pool_max": 8}}}
    s = connection_settings(cfg, {"API_WRITER_PASSWORD": "secret1", "POSTGRES_PORT": "5433",
                                  "VELOS_DB_HOST": "postgres"})
    assert (s["host"], s["port"], s["user"], s["pool_max"]) == ("postgres", 5433, "api_writer", 8)
    assert "password=secret1" in conninfo(s)


def test_mot_de_passe_absent_refuse():
    with pytest.raises(ConfigError):
        connection_settings({}, {})


def test_requete_et_predictions_enregistrees_ensemble():
    pool = FakePool()
    store = PostgresTraceStore(pool)
    request = trace()
    p = PredictionTrace(uuid.uuid4(), {"hr": 17}, 412.7)
    store.record_request(request, [p])
    (sql1, params1), (sql2, params2) = pool.conn.statements
    assert "tracing.api_requests" in sql1 and params1[0] == request.request_id
    assert "tracing.predictions" in sql2
    assert params2[1] == request.request_id and json.loads(params2[3]) == {"hr": 17}


def test_feedback_sur_prediction_inconnue():
    store = PostgresTraceStore(FakePool(fail=ForeignKeyError("violates foreign key constraint")))
    with pytest.raises(UnknownPrediction):
        store.record_feedback(uuid.uuid4(), 10.0)


def test_second_feedback_devient_duplicate_feedback():
    store = PostgresTraceStore(FakePool(fail=UniqueViolationError("duplicate key value violates unique constraint")))
    with pytest.raises(DuplicateFeedback):
        store.record_feedback(uuid.uuid4(), 10.0)


def test_panne_de_base_devient_store_unavailable_sans_mot_de_passe():
    store = PostgresTraceStore(FakePool(fail=ConnectionError("connexion refusée")))
    with pytest.raises(StoreUnavailable) as exc:
        store.record_request(trace(), [])
    assert "password" not in str(exc.value)
    assert store.ping() is False


def test_fermeture_du_pool():
    pool = FakePool()
    PostgresTraceStore(pool).close()
    assert pool.closed


@pytest.mark.skipif(os.environ.get("VELOS_PG_TESTS") != "1", reason="nécessite PostgreSQL (VELOS_PG_TESTS=1)")
def test_integration_vraie_base():
    store = PostgresTraceStore.from_config({"api": {"postgres": {}}})
    try:
        store.pool.wait(timeout=10)   # laisse au pool le temps d'ouvrir sa première connexion
        assert store.ping()
        request = RequestTrace(uuid.uuid4(), "/test-integration", 200, 1.0, 0, None)
        p = PredictionTrace(uuid.uuid4(), {"hr": 1}, 1.0)
        store.record_request(request, [p])
        assert isinstance(store.record_feedback(p.prediction_id, 2.0), uuid.UUID)
        with pytest.raises(DuplicateFeedback):   # contrainte UNIQUE (10-schema.sql ou 30-feedback-unique.sql)
            store.record_feedback(p.prediction_id, 3.0)
        with pytest.raises(UnknownPrediction):
            store.record_feedback(uuid.uuid4(), 1.0)
    finally:
        store.close()
