"""API FastAPI de bout en bout (sans MLflow ni PostgreSQL : faux modèle et stockage en mémoire)."""
import uuid
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from velos.api.app import create_app
from velos.api.persistence import InMemoryTraceStore
from velos.errors import RegistryError

from test_api_service import VALID, handle

CFG = {"api": {"max_instances": 5}}


def make_client(loader, store=None):
    store = store or InMemoryTraceStore()
    app = create_app(cfg=CFG, model_loader=loader, store=store)
    return TestClient(app), store


@pytest.fixture
def api():
    """Client avec un modèle chargé (version 4). Le `with` déclenche le démarrage (lifespan)."""
    client, store = make_client(lambda cfg: handle(version=4))
    with client:
        yield client, store


def no_model(cfg):
    raise RegistryError("alias champion absent")


def test_health_indique_la_version(api):
    client, _ = api
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok" and r.json()["model"]["version"] == 4


def test_predict_nominal_version_et_traces(api):
    client, store = api
    r = client.post("/predict", json={"instances": [VALID, VALID]})
    assert r.status_code == 200
    body = r.json()
    assert body["model"] == {"name": "velos-demand-cnt", "version": 4, "alias": "champion",
                             "qualification": "production-ready"}
    assert len(body["predictions"]) == 2 and body["predictions"][0]["cnt"] == 123.46
    assert r.headers["X-Request-ID"] == body["request_id"]
    # La requête et ses deux prédictions sont tracées, avec la version et la durée.
    trace = store.requests[-1]
    assert (trace.endpoint, trace.status_code, trace.model_version, trace.error_code) == ("/predict", 200, 4, None)
    assert trace.latency_ms > 0 and len(store.predictions) == 2


def test_entree_invalide_422_au_format_du_contrat(api):
    client, store = api
    r = client.post("/predict", json={"instances": [{**VALID, "hr": 25}]})
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "invalid_input" and any("hr" in p for p in err["problems"])
    assert store.requests[-1].error_code == "invalid_input" and not store.predictions


@pytest.mark.parametrize("corps", [{}, {"instances": []}, {"instances": [VALID], "extra": 1},
                                   {"instances": [{**VALID, "hr": {"a": 1}}]}])
def test_corps_mal_forme_422(api, corps):
    client, _ = api
    r = client.post("/predict", json=corps)
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_input"


def test_json_illisible_422(api):
    client, _ = api
    r = client.post("/predict", content=b"{pas du json", headers={"Content-Type": "application/json"})
    assert r.status_code == 422


def test_cible_en_entree_refusee(api):
    client, _ = api
    r = client.post("/predict", json={"instances": [{**VALID, "cnt": 400}]})
    assert r.status_code == 422


def test_lot_trop_grand_refuse(api):
    client, _ = api
    r = client.post("/predict", json={"instances": [VALID] * 6})
    assert r.status_code == 422


def test_sans_modele_l_api_demarre_mais_repond_503():
    client, store = make_client(no_model)
    with client:
        assert client.get("/health").status_code == 503
        r = client.post("/predict", json={"instances": [VALID]})
    assert r.status_code == 503 and r.json()["error"]["code"] == "model_unavailable"
    assert store.requests[-1].error_code == "model_unavailable"


def test_erreur_interne_500_sans_trace_python():
    class Broken:
        def predict(self, df):
            raise ZeroDivisionError("détail interne qui ne doit pas sortir")
    client, store = make_client(lambda cfg: replace(handle(), model=Broken()))
    with client:
        r = client.post("/predict", json={"instances": [VALID]})
    assert r.status_code == 500
    assert "ZeroDivision" not in r.text and "détail interne" not in r.text
    assert r.json()["error"]["code"] == "internal" and store.requests[-1].error_code == "internal"


def test_feedback_nominal_puis_inconnu(api):
    client, store = api
    pred_id = client.post("/predict", json={"instances": [VALID]}).json()["predictions"][0]["prediction_id"]
    r = client.post("/feedback", json={"prediction_id": pred_id, "observed_cnt": 130})
    assert r.status_code == 201 and len(store.feedbacks) == 1
    r = client.post("/feedback", json={"prediction_id": str(uuid.uuid4()), "observed_cnt": 1})
    assert r.status_code == 404 and r.json()["error"]["code"] == "unknown_prediction"


def test_second_feedback_sur_la_meme_prediction_refuse_409(api):
    client, store = api
    pred_id = client.post("/predict", json={"instances": [VALID]}).json()["predictions"][0]["prediction_id"]
    assert client.post("/feedback", json={"prediction_id": pred_id, "observed_cnt": 130}).status_code == 201
    r = client.post("/feedback", json={"prediction_id": pred_id, "observed_cnt": 999})
    assert r.status_code == 409 and r.json()["error"]["code"] == "feedback_exists"
    assert len(store.feedbacks) == 1 and store.feedbacks[0][2] == 130   # la première valeur est conservée


def test_feedback_negatif_refuse(api):
    client, _ = api
    r = client.post("/feedback", json={"prediction_id": str(uuid.uuid4()), "observed_cnt": -3})
    assert r.status_code == 422


def test_panne_de_la_base_les_predictions_continuent(api):
    client, store = api
    store.available = False
    assert client.get("/health").json()["status"] == "degraded"
    assert client.post("/predict", json={"instances": [VALID]}).status_code == 200
    r = client.post("/feedback", json={"prediction_id": str(uuid.uuid4()), "observed_cnt": 1})
    assert r.status_code == 503 and r.json()["error"]["code"] == "storage_unavailable"


def test_rechargement_change_de_version_sans_redemarrer():
    versions = iter([4, 5])
    client, store = make_client(lambda cfg: handle(version=next(versions)))
    with client:
        r = client.post("/model/reload")
        assert r.status_code == 200 and r.json()["previous_version"] == 4
        assert client.post("/predict", json={"instances": [VALID]}).json()["model"]["version"] == 5
    assert [m["version"] for m in store.model_loads] == [4, 5]


def test_rechargement_en_echec_conserve_le_modele():
    calls = {"n": 0}

    def loader(cfg):
        calls["n"] += 1
        if calls["n"] > 1:
            raise RegistryError("registre injoignable")
        return handle(version=4)
    client, _ = make_client(loader)
    with client:
        assert client.post("/model/reload").status_code == 503
        assert client.get("/health").json()["model"]["version"] == 4
