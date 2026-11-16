"""Prédire avec un run publié, sans réentraîner (commande de prédiction du TP1)."""
from __future__ import annotations

import os
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from .contract import validate_inference_frame
from .errors import ConfigError, RegistryError
from .evaluation import is_complete


def load_run_model(run_dir):
    run_dir = Path(run_dir)
    if not is_complete(run_dir):
        raise ConfigError(f"Run introuvable ou incomplet (publication.json absent ou invalide) : {run_dir}")
    return joblib.load(run_dir / "model.joblib")


def predict_frame(model, df: pd.DataFrame) -> np.ndarray:
    """Valide l'entrée (contrat) puis prédit. Le nombre de locations ne peut pas être négatif."""
    X = validate_inference_frame(df)
    return np.clip(model.predict(X), 0, None)


def predict_file(run_dir, input_csv, output_csv) -> Path:
    model = load_run_model(run_dir)
    df = pd.read_csv(input_csv)
    out = pd.DataFrame({"cnt_pred": predict_frame(model, df)})
    if "instant" in df.columns:
        out.insert(0, "instant", df["instant"].to_numpy())
    output_csv = Path(output_csv)
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(output_csv, index=False, lineterminator="\n")
    return output_csv


def load_alias_model(cfg: dict, alias: str | None = None, loader=None):
    """Charge le modèle désigné par l'ALIAS du registre (ex. models:/velos-demand-cnt@champion).
    C'est la méthode que l'API doit utiliser. Retourne (modèle, uri, version)."""
    t = cfg["tracking"]
    alias = alias or cfg["qualification"]["alias"]
    uri = f"models:/{t['model_name']}@{alias}"
    server = os.environ.get(t["uri_env"], t["default_uri"])
    try:
        if loader is None:
            import mlflow
            from mlflow import MlflowClient
            mlflow.set_tracking_uri(server)
            mlflow.set_registry_uri(server)
            version = int(MlflowClient(tracking_uri=server, registry_uri=server)
                          .get_model_version_by_alias(t["model_name"], alias).version)
            model = mlflow.pyfunc.load_model(uri)
        else:
            model, version = loader(uri)
    except Exception as exc:
        raise RegistryError(f"Impossible de charger {uri} depuis {server} : {type(exc).__name__}: {exc}. "
                            "L'alias existe-t-il ? (velos versions, velos select)") from exc
    return model, uri, version


def predict_file_alias(cfg: dict, input_csv, output_csv, alias: str | None = None, loader=None):
    model, uri, version = load_alias_model(cfg, alias, loader)
    df = pd.read_csv(input_csv)
    pred = np.clip(np.asarray(model.predict(validate_inference_frame(df))), 0, None)
    out = pd.DataFrame({"cnt_pred": pred, "model_version": version})
    if "instant" in df.columns:
        out.insert(0, "instant", df["instant"].to_numpy())
    output_csv = Path(output_csv)
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(output_csv, index=False, lineterminator="\n")
    return output_csv, uri, version
