"""Règles de l'API testées sans serveur : validation, prédiction, stockage des traces."""
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest

from velos.api.model_handle import ModelHandle
from velos.api.persistence import (InMemoryTraceStore, PredictionTrace, RequestTrace, StoreUnavailable,
                                   UnknownPrediction, make_store)
from velos.api.service import ApiError, error_body, predict_instances, require_model
from velos.errors import ConfigError

ROOT = Path(__file__).resolve().parents[1]
VALID = json.loads((ROOT / "contract" / "example_request.json").read_text(encoding="utf-8"))["instances"][0]


class FakeModel:
    """Prédit une valeur fixe ; une valeur négative doit être ramenée à 0 (pas de location négative)."""

    def __init__(self, value=123.456):
        self.value = value

    def predict(self, df):
        return np.full(len(df), self.value)


def handle(value=123.456, version=4):
    return ModelHandle(model=FakeModel(value), name="velos-demand-cnt", alias="champion", version=version,
                       uri="models:/velos-demand-cnt@champion", qualification="production-ready",
                       data_origin="uci", loaded_at=datetime.now(timezone.utc))


def test_prediction_valide_une_trace_par_instance():
    traces = predict_instances(handle(), [VALID, VALID], max_instances=10)
    assert len(traces) == 2
    assert traces[0].prediction_id != traces[1].prediction_id
    assert traces[0].predicted_cnt == 123.46
    assert traces[0].features == VALID          # seules les 12 features sont conservées


def test_prediction_negative_ramenee_a_zero():
    assert predict_instances(handle(value=-5.0), [VALID], 10)[0].predicted_cnt == 0.0


def test_valeur_hors_plage_refusee_avec_le_probleme():
    with pytest.raises(ApiError) as exc:
        predict_instances(handle(), [{**VALID, "hr": 25}], 10)
    assert exc.value.status == 422 and exc.value.code == "invalid_input"
    assert any("hr" in p for p in exc.value.problems)


def test_texte_non_numerique_refuse():
    with pytest.raises(ApiError) as exc:
        predict_instances(handle(), [{**VALID, "temp": "chaud"}], 10)
    assert exc.value.status == 422


def test_feature_manquante_refusee():
    incomplete = {k: v for k, v in VALID.items() if k != "weathersit"}
    with pytest.raises(ApiError) as exc:
        predict_instances(handle(), [incomplete], 10)
    assert "weathersit" in " ".join(exc.value.problems)


@pytest.mark.parametrize("colonne", ["cnt", "casual", "registered"])
def test_cible_interdite_en_entree(colonne):
    with pytest.raises(ApiError) as exc:
        predict_instances(handle(), [{**VALID, colonne: 10}], 10)
    assert colonne in exc.value.problems[0]


def test_trop_d_instances_refuse():
    with pytest.raises(ApiError) as exc:
        predict_instances(handle(), [VALID] * 3, max_instances=2)
    assert exc.value.status == 422


def test_sans_modele_503():
    with pytest.raises(ApiError) as exc:
        require_model(None)
    assert exc.value.status == 503 and exc.value.code == "model_unavailable"


def test_format_d_erreur_du_contrat():
    attendu = json.loads((ROOT / "contract" / "error_example.json").read_text(encoding="utf-8"))
    body = error_body(uuid.UUID(int=0), "invalid_input", "Entrées invalides", ["hr: 25 hors de [0, 23]"])
    assert body == attendu


def test_stockage_requete_puis_feedback():
    store = InMemoryTraceStore()
    p = PredictionTrace(uuid.uuid4(), VALID, 10.0)
    store.record_request(RequestTrace(uuid.uuid4(), "/predict", 200, 3.2, 4, None), [p])
    feedback_id = store.record_feedback(p.prediction_id, 12.0)
    assert store.feedbacks == [(feedback_id, p.prediction_id, 12.0)]
    with pytest.raises(UnknownPrediction):
        store.record_feedback(uuid.uuid4(), 1.0)


def test_panne_de_stockage_signalee():
    store = InMemoryTraceStore()
    store.available = False
    assert store.ping() is False
    with pytest.raises(StoreUnavailable):
        store.record_request(RequestTrace(uuid.uuid4(), "/predict", 200, 1.0, 4, None), [])


def test_type_de_stockage_inconnu_refuse():
    with pytest.raises(ConfigError):
        make_store({"api": {"trace_store": "fichier"}})
