"""API de prédiction : POST /predict, POST /feedback, GET /health, POST /model/reload.

Lancement (depuis la racine du projet) :
    uv run uvicorn velos.api.app:create_app --factory --host 127.0.0.1 --port 8000

Principes :
  - le modèle est chargé UNE FOIS au démarrage, par son alias (models:/velos-demand-cnt@champion) ;
    sans modèle, l'API démarre quand même mais répond 503 (et /health le signale) ;
  - chaque réponse indique la version du modèle utilisée ;
  - chaque requête est tracée (identifiant, durée, statut, version, code d'erreur) avec ses prédictions ;
  - une panne de la base de traces ne bloque pas les prédictions : elle est journalisée et /health
    passe en « degraded » (choix justifié dans la synthèse OPS).
"""
from __future__ import annotations

import logging
import os
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware

from ..config import load_config
from ..errors import VelosError
from .model_handle import ModelHandle, load_champion
from .persistence import RequestTrace, StoreUnavailable, TraceStore, UnknownPrediction, make_store
from .schemas import FeedbackRequest, PredictRequest
from .service import ApiError, error_body, predict_instances, require_model

LOGGER = logging.getLogger("velos.api")
TRACED_PATHS = {"/predict", "/feedback", "/model/reload"}   # /health est appelé trop souvent pour être tracé


def create_app(cfg: dict | None = None, model_loader=None, store: TraceStore | None = None) -> FastAPI:
    """Assemble l'application. Les paramètres servent aux tests (faux modèle, stockage en mémoire)."""
    cfg = cfg or load_config(os.environ.get("VELOS_CONFIG", "config.json"))
    model_loader = model_loader or load_champion
    store = store or make_store(cfg)
    max_instances = int(cfg.get("api", {}).get("max_instances", 1000))

    def load_model() -> ModelHandle:
        """Charge le champion et note ce chargement dans les traces. Lève VelosError en cas d'échec."""
        handle = model_loader(cfg)
        LOGGER.info("Modèle chargé : %s (version %s, %s)", handle.uri, handle.version, handle.qualification)
        try:
            store.record_model_load(handle.name, handle.version, handle.alias,
                                    handle.qualification, handle.data_origin)
        except StoreUnavailable as exc:
            LOGGER.error("Chargement du modèle non tracé : %s", exc)
        return handle

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        logging.basicConfig(level=logging.INFO)
        try:
            app.state.model = await run_in_threadpool(load_model)
        except VelosError as exc:
            LOGGER.error("Démarrage SANS modèle (les prédictions répondront 503) : %s", exc)
        yield

    app = FastAPI(title="Vélos — API de prédiction",
                  description="Prévision du nombre de locations de vélos par heure (UCI Bike Sharing).",
                  version="1.0.0", lifespan=lifespan)
    app.state.model = None

    # ---- Traçage de chaque requête --------------------------------------------------------------
    async def trace_requests(request: Request, call_next):
        request.state.request_id = uuid.uuid4()
        request.state.error_code = None
        request.state.model_version = None
        request.state.predictions = []
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:   # erreur imprévue : détail dans les logs, jamais dans la réponse
            LOGGER.exception("Erreur interne (request_id=%s)", request.state.request_id)
            request.state.error_code = "internal"
            response = JSONResponse(status_code=500, content=error_body(
                request.state.request_id, "internal", "Erreur interne du service"))
        latency_ms = (time.perf_counter() - started) * 1000
        response.headers["X-Request-ID"] = str(request.state.request_id)
        if request.url.path in TRACED_PATHS:
            trace = RequestTrace(request.state.request_id, request.url.path, response.status_code,
                                 latency_ms, request.state.model_version, request.state.error_code)
            try:
                await run_in_threadpool(store.record_request, trace, request.state.predictions)
            except StoreUnavailable as exc:
                LOGGER.error("Trace perdue (request_id=%s) : %s", request.state.request_id, exc)
        return response

    app.add_middleware(BaseHTTPMiddleware, dispatch=trace_requests)

    # ---- Traduction des erreurs en JSON ---------------------------------------------------------
    @app.exception_handler(ApiError)
    async def on_api_error(request: Request, exc: ApiError):
        request.state.error_code = exc.code
        return JSONResponse(status_code=exc.status,
                            content=error_body(request.state.request_id, exc.code, exc.message, exc.problems))

    @app.exception_handler(RequestValidationError)
    async def on_bad_body(request: Request, exc: RequestValidationError):
        problems = [f"{'.'.join(str(p) for p in err['loc'] if p != 'body')}: {err['msg']}" for err in exc.errors()]
        request.state.error_code = "invalid_input"
        return JSONResponse(status_code=422,
                            content=error_body(request.state.request_id, "invalid_input", "Entrées invalides", problems))

    # ---- Routes ---------------------------------------------------------------------------------
    @app.get("/health")
    def health(response: Response) -> dict:
        handle: ModelHandle | None = app.state.model
        database = "ok" if store.ping() else "unavailable"
        if handle is None:
            response.status_code = 503
            status = "unavailable"
        else:
            status = "ok" if database == "ok" else "degraded"
        return {"status": status, "model": handle.info() if handle else None, "database": database}

    @app.post("/predict")
    def predict(body: PredictRequest, request: Request) -> dict:
        handle = require_model(app.state.model)       # copie locale : un rechargement ne la change pas
        request.state.model_version = handle.version
        predictions = predict_instances(handle, body.instances, max_instances)
        request.state.predictions = predictions          # enregistrées avec la requête par trace_requests
        return {
            "request_id": str(request.state.request_id),
            "model": handle.info(),
            "predictions": [{"prediction_id": str(p.prediction_id), "cnt": p.predicted_cnt} for p in predictions],
        }

    @app.post("/feedback", status_code=201)
    def feedback(body: FeedbackRequest, request: Request) -> dict:
        try:
            feedback_id = store.record_feedback(body.prediction_id, body.observed_cnt)
        except UnknownPrediction as exc:
            raise ApiError(404, "unknown_prediction", "Prédiction inconnue",
                           [f"prediction_id {body.prediction_id} introuvable"]) from exc
        except StoreUnavailable as exc:
            raise ApiError(503, "storage_unavailable", "Stockage des feedbacks indisponible, réessayez plus tard") from exc
        return {"request_id": str(request.state.request_id), "feedback_id": str(feedback_id),
                "prediction_id": str(body.prediction_id)}

    @app.post("/model/reload")
    def reload_model(request: Request) -> dict:
        """Recharge le modèle désigné par l'alias (ex. après `velos select` ou `velos rollback`)
        sans redémarrer l'API. En cas d'échec, le modèle actuel est conservé."""
        try:
            handle = load_model()
        except VelosError as exc:
            raise ApiError(503, "model_unavailable", "Rechargement impossible : le modèle actuel est conservé",
                           [str(exc)]) from exc
        previous = app.state.model
        app.state.model = handle
        request.state.model_version = handle.version
        return {"request_id": str(request.state.request_id), "model": handle.info(),
                "previous_version": previous.version if previous else None}

    return app
