"""Étape 4 : mesurer, puis PUBLIER le run (atomiquement) et journaliser dans le suivi.

Publication atomique : tout est écrit dans un dossier « .partial » puis renommé en une seule opération.
`publication.json` est écrit en DERNIER dans ce dossier : sans lui, un run n'est pas considéré complet.
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score

from .contract import FEATURES
from .stages import EvaluationResult, TrainedModels
from .state import now_utc, sha256_file, write_json_atomic


def regression_metrics(y_true, y_pred) -> dict:
    return {
        "mae": float(mean_absolute_error(y_true, y_pred)),
        "rmse": float(np.sqrt(mean_squared_error(y_true, y_pred))),
        "r2": float(r2_score(y_true, y_pred)),
    }


def segment_metrics(frame: pd.DataFrame, y_true, pred_model, pred_base, column: str) -> dict:
    out = {}
    for value in sorted(frame[column].unique()):
        mask = (frame[column] == value).to_numpy()
        out[str(value)] = {
            "rows": int(mask.sum()),
            "mae": float(mean_absolute_error(y_true[mask], pred_model[mask])),
            "baseline_mae": float(mean_absolute_error(y_true[mask], pred_base[mask])),
        }
    return out


def is_complete(run_dir) -> bool:
    """Un run est complet seulement si publication.json existe ET que les empreintes des fichiers correspondent."""
    run_dir = Path(run_dir)
    marker = run_dir / "publication.json"
    if not marker.is_file():
        return False
    try:
        pub = json.loads(marker.read_text(encoding="utf-8"))
        for name, info in pub["files"].items():
            f = run_dir / name
            if not f.is_file() or f.stat().st_size == 0 or sha256_file(f) != info["sha256"]:
                return False
    except (json.JSONDecodeError, KeyError):
        return False
    return True


def evaluate_models(trained: TrainedModels) -> EvaluationResult:
    prepared = trained.prepared
    data = prepared.ingested
    cfg = data.cfg
    policy_col = "weathersit"

    pred_test, base_test = trained.pipeline.predict(prepared.X_test), trained.baseline.predict(prepared.X_test)
    pred_val, base_val = trained.pipeline.predict(prepared.X_val), trained.baseline.predict(prepared.X_val)
    y_val = prepared.y_val.to_numpy()
    metrics = {
        "test": regression_metrics(prepared.y_test, pred_test),
        "baseline_test": regression_metrics(prepared.y_test, base_test),
        "validation": regression_metrics(y_val, pred_val),
        "baseline_validation": regression_metrics(y_val, base_val),
    }
    segments = {policy_col: segment_metrics(prepared.val_frame, y_val, pred_val, base_val, policy_col)}
    evidence = {
        "schema": 1,
        "model_name": cfg["tracking"]["model_name"],
        "data_origin": data.provenance["origin"],
        "lot_id": data.lot_id,
        "snapshot_sha256": data.provenance["snapshot_sha256"],
        "corpus_sha256": data.provenance["corpus_sha256"],
        "metrics": metrics,
        "segments": segments,
        "created_at_utc": now_utc(),
    }

    run_dir = Path(data.run_dir)
    partial = run_dir.with_name(run_dir.name + ".partial")
    if partial.exists():
        shutil.rmtree(partial)
    partial.mkdir(parents=True)
    joblib.dump(trained.pipeline, partial / "model.joblib")
    write_json_atomic(partial / "metrics.json", metrics)
    write_json_atomic(partial / "evidence.json", evidence)
    write_json_atomic(partial / "provenance.json", data.provenance)
    write_json_atomic(partial / "quality-reports.json", data.quality_reports)
    write_json_atomic(partial / "config.json", {k: v for k, v in cfg.items() if not k.startswith("_")})
    splits = pd.concat([
        pd.DataFrame({"instant": prepared.train_ids, "partition": "train"}),
        pd.DataFrame({"instant": prepared.test_ids, "partition": "test"}),
    ])
    splits.to_csv(partial / "splits.csv", index=False, lineterminator="\n")
    files = {p.name: {"sha256": sha256_file(p), "bytes": p.stat().st_size} for p in sorted(partial.iterdir())}
    write_json_atomic(partial / "publication.json", {"schema": 1, "completed_at_utc": now_utc(), "files": files})
    os.replace(partial, run_dir)   # publication atomique

    tracker = data.tracker
    tracker.log_metrics({f"{split}.{name}": value for split, vals in metrics.items() for name, value in vals.items()})
    tracker.log_artifacts(run_dir)
    model_uri = tracker.log_model(trained.pipeline, "model", prepared.X_train.head(20))
    write_json_atomic(run_dir / "tracking.json", {"mlflow_run_id": tracker.run_id, "model_uri": model_uri})
    return EvaluationResult(run_dir=run_dir, metrics=metrics, evidence=evidence,
                            model_uri=model_uri, mlflow_run_id=tracker.run_id, ingested=data)
