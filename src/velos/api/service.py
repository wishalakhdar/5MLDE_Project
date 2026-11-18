"""Règles de l'API, sans dépendre de FastAPI : on peut les tester directement.

Les erreurs sont levées sous forme d'ApiError (statut HTTP + code stable + détails) ;
app.py se contente de les traduire en réponse JSON (format de contract/error_example.json).
"""
from __future__ import annotations

import uuid

import pandas as pd

from ..contract import FEATURES, LEAK_COLUMNS
from ..errors import InvalidInput
from ..predict import predict_frame
from .model_handle import ModelHandle
from .persistence import PredictionTrace


class ApiError(Exception):
    """Erreur destinée au client : jamais de trace Python, seulement un code et un message."""

    def __init__(self, status: int, code: str, message: str, problems: list[str] | None = None):
        super().__init__(message)
        self.status, self.code, self.message, self.problems = status, code, message, problems or []


def error_body(request_id, code: str, message: str, problems: list[str] | None = None) -> dict:
    body = {"request_id": str(request_id), "error": {"code": code, "message": message}}
    if problems:
        body["error"]["problems"] = problems
    return body


def require_model(handle: ModelHandle | None) -> ModelHandle:
    if handle is None:
        raise ApiError(503, "model_unavailable",
                       "Aucun modèle disponible : alias absent ou registre MLflow injoignable")
    return handle


def predict_instances(handle: ModelHandle, instances: list[dict], max_instances: int) -> list[PredictionTrace]:
    """Valide les instances (contrat d'Aicha) puis prédit. Retourne une trace par instance."""
    if len(instances) > max_instances:
        raise ApiError(422, "invalid_input", "Entrées invalides",
                       [f"trop d'instances : {len(instances)} (maximum {max_instances} par requête)"])
    # La cible et ses composantes ne doivent jamais être envoyées : le contrat les interdit (fuite).
    forbidden = sorted({key for inst in instances for key in inst} & set(LEAK_COLUMNS))
    if forbidden:
        raise ApiError(422, "invalid_input", "Entrées invalides", [f"colonnes interdites en entrée : {forbidden}"])
    try:
        values = predict_frame(handle.model, pd.DataFrame(instances))   # validation + prédiction (clip >= 0)
    except InvalidInput as exc:
        raise ApiError(422, "invalid_input", "Entrées invalides", exc.problems) from exc
    return [
        PredictionTrace(prediction_id=uuid.uuid4(),
                        features={f: inst.get(f) for f in FEATURES},
                        predicted_cnt=round(float(v), 2))
        for inst, v in zip(instances, values)
    ]
