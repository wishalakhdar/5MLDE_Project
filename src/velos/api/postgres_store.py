"""Traces d'utilisation dans PostgreSQL (schéma `tracing`, créé par infra/postgres/10-schema.sql).

Connexion avec le rôle `api_writer` (INSERT/SELECT seulement). Le mot de passe n'est JAMAIS dans la
configuration : il est lu dans la variable d'environnement API_WRITER_PASSWORD (fichier .env, non versionné).

Un « pool » garde quelques connexions ouvertes et les prête aux requêtes : ouvrir une connexion
PostgreSQL à chaque requête coûterait plusieurs millisecondes. Sa taille maximale (`pool_max`) est un
paramètre de dimensionnement, comparé pendant les tests de charge.

Toute panne (base arrêtée, mauvais mot de passe, délai dépassé) devient StoreUnavailable : app.py décide
ensuite quoi faire (prédictions maintenues, feedbacks refusés en 503).
"""
from __future__ import annotations

import json
import logging
import os
import uuid

from ..errors import ConfigError
from .persistence import (DuplicateFeedback, PredictionTrace, RequestTrace, StoreUnavailable, TraceStore,
                          UnknownPrediction)

PASSWORD_ENV = "API_WRITER_PASSWORD"
FOREIGN_KEY_VIOLATION = "23503"   # codes d'erreur SQL standard
UNIQUE_VIOLATION = "23505"
LOGGER = logging.getLogger("velos.api.postgres")
# 127.0.0.1 plutôt que localhost : sous Windows, « localhost » est d'abord essayé en IPv6 (::1), où Docker
# n'écoute pas ; chaque connexion attendait alors connect_timeout (3 s) avant de passer en IPv4.
DEFAULTS = {"host": "127.0.0.1", "port": 5432, "database": "velos", "user": "api_writer",
            "pool_min": 1, "pool_max": 4, "timeout_seconds": 3}


def connection_settings(cfg: dict, environ=os.environ) -> dict:
    """Réglages de connexion : config.json (api.postgres), surchargés par l'environnement.
    VELOS_DB_HOST / POSTGRES_PORT permettent le même code sur le poste (localhost:5433 par ex.)
    et dans Docker (postgres:5432) sans modifier la configuration."""
    settings = {**DEFAULTS, **cfg.get("api", {}).get("postgres", {})}
    settings["host"] = environ.get("VELOS_DB_HOST", settings["host"])
    settings["port"] = int(environ.get("POSTGRES_PORT", settings["port"]))
    password = environ.get(PASSWORD_ENV)
    if not password:
        raise ConfigError(f"Variable d'environnement {PASSWORD_ENV} absente : lancez l'API avec --env-file .env")
    settings["password"] = password
    return settings


def conninfo(settings: dict) -> str:
    return (f"host={settings['host']} port={settings['port']} dbname={settings['database']} "
            f"user={settings['user']} password={settings['password']} "
            f"connect_timeout={settings['timeout_seconds']} application_name=velos-api")


class PostgresTraceStore(TraceStore):
    def __init__(self, pool, timeout_seconds: float = 3):
        self.pool = pool
        self.timeout = timeout_seconds

    @classmethod
    def from_config(cls, cfg: dict) -> "PostgresTraceStore":
        from psycopg_pool import ConnectionPool
        s = connection_settings(cfg)
        # open=False puis open(wait=False) : l'API démarre même si la base est arrêtée (/health = degraded) ;
        # le pool se reconnecte tout seul quand la base revient.
        pool = ConnectionPool(conninfo(s), min_size=s["pool_min"], max_size=s["pool_max"], open=False,
                              timeout=s["timeout_seconds"], name="velos-traces")
        pool.open(wait=False)
        return cls(pool, s["timeout_seconds"])

    def _connection(self):
        """Emprunte une connexion au pool. Le bloc `with` valide la transaction (COMMIT) s'il se termine
        normalement et l'annule (ROLLBACK) en cas d'exception."""
        return self.pool.connection(timeout=self.timeout)

    def record_request(self, request: RequestTrace, predictions: list[PredictionTrace]) -> None:
        try:
            with self._connection() as conn:   # requête + prédictions : une seule transaction (tout ou rien)
                conn.execute(
                    "INSERT INTO tracing.api_requests (request_id, endpoint, status_code, latency_ms,"
                    " model_version, error_code) VALUES (%s, %s, %s, %s, %s, %s)",
                    (request.request_id, request.endpoint, request.status_code, request.latency_ms,
                     request.model_version, request.error_code))
                if predictions:
                    with conn.cursor() as cur:
                        cur.executemany(
                            "INSERT INTO tracing.predictions (prediction_id, request_id, model_version, features,"
                            " predicted_cnt) VALUES (%s, %s, %s, %s::jsonb, %s)",
                            [(p.prediction_id, request.request_id, request.model_version,
                              json.dumps(p.features), p.predicted_cnt) for p in predictions])
        except Exception as exc:
            raise _unavailable(exc) from exc

    def record_feedback(self, prediction_id: uuid.UUID, observed_cnt: float) -> uuid.UUID:
        feedback_id = uuid.uuid4()
        try:
            with self._connection() as conn:
                conn.execute("INSERT INTO tracing.feedbacks (feedback_id, prediction_id, observed_cnt)"
                             " VALUES (%s, %s, %s)", (feedback_id, prediction_id, observed_cnt))
        except Exception as exc:
            # 23503 = violation de clé étrangère : la prédiction référencée n'existe pas.
            if getattr(exc, "sqlstate", None) == FOREIGN_KEY_VIOLATION:
                raise UnknownPrediction(f"prédiction inconnue : {prediction_id}") from exc
            # 23505 = violation d'unicité : un feedback existe déjà pour cette prédiction.
            if getattr(exc, "sqlstate", None) == UNIQUE_VIOLATION:
                raise DuplicateFeedback(f"feedback déjà enregistré pour la prédiction {prediction_id}") from exc
            raise _unavailable(exc) from exc
        return feedback_id

    def record_model_load(self, name, version, alias, qualification, data_origin) -> None:
        try:
            with self._connection() as conn:
                conn.execute("INSERT INTO tracing.model_versions (model_name, version, alias, qualification,"
                             " data_origin) VALUES (%s, %s, %s, %s, %s)",
                             (name, version, alias, qualification, data_origin))
        except Exception as exc:
            raise _unavailable(exc) from exc

    def ping(self) -> bool:
        try:
            with self._connection() as conn:
                conn.execute("SELECT 1")
            return True
        except Exception as exc:   # la cause est journalisée : indispensable pour diagnostiquer une panne
            LOGGER.warning("Base de traces injoignable : %s", _unavailable(exc))
            return False

    def close(self) -> None:
        self.pool.close()


def _unavailable(exc: Exception) -> StoreUnavailable:
    # Le message ne contient jamais la chaîne de connexion (donc jamais le mot de passe).
    return StoreUnavailable(f"PostgreSQL indisponible : {type(exc).__name__}: {str(exc).splitlines()[0] if str(exc) else ''}")
