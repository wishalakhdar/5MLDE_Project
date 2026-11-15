"""Étape 3 : entraîner le modèle ET sa baseline (le niveau à battre : prédire la moyenne)."""
from __future__ import annotations

import time

from sklearn.base import clone
from sklearn.dummy import DummyRegressor
from sklearn.ensemble import RandomForestRegressor
from sklearn.pipeline import Pipeline

from .stages import PreparedData, TrainedModels


def build_pipeline(model_cfg: dict, seed: int) -> Pipeline:
    from .preprocessing import build_preprocessor
    forest = RandomForestRegressor(
        n_estimators=model_cfg["n_estimators"], max_depth=model_cfg["max_depth"],
        min_samples_leaf=model_cfg["min_samples_leaf"], n_jobs=model_cfg["n_jobs"], random_state=seed,
    )
    return Pipeline([("prep", build_preprocessor()), ("model", forest)])


def train_models(prepared: PreparedData) -> TrainedModels:
    cfg = prepared.ingested.cfg
    seed = cfg["seed"]
    started = time.perf_counter()
    # Le prétraitement fait partie du pipeline : il est donc ajusté sur le train seulement (fit(X_train)).
    pipeline = build_pipeline(cfg["model"], seed)
    pipeline.fit(prepared.X_train, prepared.y_train)
    baseline = Pipeline([("prep", clone(prepared.preprocessor)), ("model", DummyRegressor(strategy="mean"))])
    baseline.fit(prepared.X_train, prepared.y_train)
    seconds = time.perf_counter() - started
    prepared.ingested.tracker.log_params({f"model.{k}": v for k, v in cfg["model"].items()} | {"seed": seed})
    prepared.ingested.tracker.log_metrics({"train.seconds": seconds})
    return TrainedModels(prepared=prepared, pipeline=pipeline, baseline=baseline, train_seconds=seconds)
