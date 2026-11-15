"""Objets échangés entre les 4 étapes. Aucune variable globale : chaque fonction reçoit le résultat de la précédente."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd


@dataclass
class IngestedData:
    cfg: dict
    snapshot: pd.DataFrame          # base + lots acceptés + lot courant
    corpus: pd.DataFrame            # corpus de qualification (fixe)
    lot: pd.DataFrame | None
    lot_id: str | None
    lot_path: Path | None
    lot_sha256: str | None
    provenance: dict
    run_dir: Path
    tracker: Any
    quality_reports: list = field(default_factory=list)


@dataclass
class PreparedData:
    ingested: IngestedData
    X_train: pd.DataFrame
    y_train: pd.Series
    X_test: pd.DataFrame
    y_test: pd.Series
    train_ids: list
    test_ids: list
    X_val: pd.DataFrame
    y_val: pd.Series
    val_frame: pd.DataFrame         # corpus complet (pour les segments)
    preprocessor: Any               # NON ajusté : l'ajustement se fait sur le train, dans l'entraînement


@dataclass
class TrainedModels:
    prepared: PreparedData
    pipeline: Any
    baseline: Any
    train_seconds: float


@dataclass
class EvaluationResult:
    run_dir: Path
    metrics: dict
    evidence: dict
    model_uri: str | None = None
    mlflow_run_id: str | None = None
    ingested: Any = None            # IngestedData : sert à commit_lot
