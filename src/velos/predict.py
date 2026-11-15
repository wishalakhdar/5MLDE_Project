"""Prédire avec un run publié, sans réentraîner (commande de prédiction du TP1)."""
from __future__ import annotations

from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from .contract import validate_inference_frame
from .errors import ConfigError
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
