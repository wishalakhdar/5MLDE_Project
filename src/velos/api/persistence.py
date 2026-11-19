"""Conservation des traces d'utilisation : requêtes, prédictions, feedbacks, chargements de modèle.

Une interface, plusieurs implémentations (même principe que registry.py) :
  - InMemoryTraceStore : tests et essais sans base ;
  - PostgresTraceStore : schéma `tracing` de PostgreSQL (postgres_store.py).
Une panne de stockage lève StoreUnavailable : l'API décide ensuite quoi faire (voir app.py).
"""
from __future__ import annotations

import threading
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass

from ..errors import ConfigError, VelosError


class StoreUnavailable(VelosError):
    """La base de traces ne répond pas (panne, réseau, identifiants)."""


class UnknownPrediction(VelosError):
    """Feedback sur une prédiction qui n'a jamais été enregistrée."""


class DuplicateFeedback(VelosError):
    """Un feedback existe déjà pour cette prédiction (un seul autorisé)."""


@dataclass(frozen=True)
class RequestTrace:
    request_id: uuid.UUID
    endpoint: str
    status_code: int
    latency_ms: float
    model_version: int | None
    error_code: str | None


@dataclass(frozen=True)
class PredictionTrace:
    prediction_id: uuid.UUID
    features: dict
    predicted_cnt: float


class TraceStore(ABC):
    @abstractmethod
    def record_request(self, request: RequestTrace, predictions: list[PredictionTrace]) -> None:
        """Enregistre la requête ET ses prédictions ensemble (tout ou rien)."""

    @abstractmethod
    def record_feedback(self, prediction_id: uuid.UUID, observed_cnt: float) -> uuid.UUID:
        """Enregistre la valeur réellement observée. Lève UnknownPrediction si l'identifiant est inconnu,
        DuplicateFeedback si un feedback existe déjà pour cette prédiction."""

    @abstractmethod
    def record_model_load(self, name: str, version: int, alias: str, qualification: str, data_origin: str) -> None:
        """Garde l'historique des modèles chargés par l'API (quelle version a servi, et quand)."""

    @abstractmethod
    def ping(self) -> bool:
        """True si le stockage répond (utilisé par /health)."""

    def close(self) -> None:
        """Libère les ressources (connexions) à l'arrêt de l'API. Rien à faire par défaut."""


class InMemoryTraceStore(TraceStore):
    """Stockage en mémoire, perdu à l'arrêt. `available=False` simule une panne de la base."""

    def __init__(self):
        self.available = True
        self.requests: list[RequestTrace] = []
        self.predictions: dict[uuid.UUID, tuple[int | None, PredictionTrace]] = {}
        self.feedbacks: list[tuple[uuid.UUID, uuid.UUID, float]] = []
        self.model_loads: list[dict] = []
        self._lock = threading.Lock()   # l'API traite plusieurs requêtes en parallèle

    def _check(self):
        if not self.available:
            raise StoreUnavailable("stockage en mémoire marqué indisponible (simulation de panne)")

    def record_request(self, request, predictions):
        self._check()
        with self._lock:
            self.requests.append(request)
            for p in predictions:
                self.predictions[p.prediction_id] = (request.model_version, p)

    def record_feedback(self, prediction_id, observed_cnt):
        self._check()
        with self._lock:
            if prediction_id not in self.predictions:
                raise UnknownPrediction(f"prédiction inconnue : {prediction_id}")
            if any(existing == prediction_id for _, existing, _ in self.feedbacks):
                raise DuplicateFeedback(f"feedback déjà enregistré pour la prédiction {prediction_id}")
            feedback_id = uuid.uuid4()
            self.feedbacks.append((feedback_id, prediction_id, observed_cnt))
            return feedback_id

    def record_model_load(self, name, version, alias, qualification, data_origin):
        self._check()
        with self._lock:
            self.model_loads.append({"name": name, "version": version, "alias": alias,
                                     "qualification": qualification, "data_origin": data_origin})

    def ping(self):
        return self.available


def make_store(cfg: dict) -> TraceStore:
    kind = cfg.get("api", {}).get("trace_store", "memory")
    if kind == "memory":
        return InMemoryTraceStore()
    if kind == "postgres":
        from .postgres_store import PostgresTraceStore
        return PostgresTraceStore.from_config(cfg)
    raise ConfigError(f"api.trace_store inconnu : {kind!r} (valeurs possibles : memory, postgres)")
