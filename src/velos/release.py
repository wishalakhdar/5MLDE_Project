"""Après l'évaluation : enregistrer la version, vérifier son rechargement, la qualifier.

Idempotent : si une version existe déjà pour ce run de pipeline, elle est réutilisée (pas de doublon
en cas de reprise après une panne du registre).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from .config import path_of
from .contract import FEATURES
from .errors import RegistryError, TransientError
from .lifecycle import qualify_version
from .qualification import Decision, load_policy
from .registry import RegistryBackend
from .state import read_store_manifest


def compare_predictions(local: np.ndarray, reloaded: np.ndarray, tolerance: float) -> dict:
    """Compare les prédictions du modèle local et du modèle rechargé depuis le registre."""
    local, reloaded = np.asarray(local, dtype=float), np.asarray(reloaded, dtype=float)
    if local.shape != reloaded.shape:
        return {"status": "echec-forme", "rows": int(len(local)), "max_abs_delta": None}
    delta = float(np.max(np.abs(local - reloaded))) if len(local) else float("nan")
    ok = np.isfinite(delta) and delta <= tolerance
    return {"status": "ok" if ok else "echec-ecart", "rows": int(len(local)), "max_abs_delta": delta}


def verify_reload(cfg: dict, run_dir, name: str, version: int, loader=None) -> dict:
    """Recharge la version depuis le registre et compare ses prédictions à celles du modèle local.
    À exécuter dans un NOUVEAU processus (voir `subprocess_reload_verifier`)."""
    import mlflow
    policy = load_policy(cfg)
    n = cfg["qualification"]["reload_sample_rows"]
    sample = pd.read_csv(path_of(cfg, "store_dir") / "qualification.csv")[FEATURES].head(n)
    local = joblib.load(Path(run_dir) / "model.joblib").predict(sample)
    load = loader or (lambda uri: mlflow.pyfunc.load_model(uri))
    try:
        reloaded = load(f"models:/{name}/{version}").predict(sample)
    except Exception as exc:  # modèle non rechargeable : verdict « rejected » plus tard
        return {"status": f"echec-chargement ({type(exc).__name__})", "rows": 0, "max_abs_delta": None}
    return compare_predictions(local, reloaded, policy["reload_tolerance"])


def subprocess_reload_verifier(cfg: dict):
    """Fabrique une fonction qui lance la vérification dans un NOUVEAU processus Python."""
    def run(run_dir, name, version) -> dict:
        proc = subprocess.run(
            [sys.executable, "-m", "velos", "--config", cfg["_config_path"], "verify-reload",
             "--run-dir", str(run_dir), "--name", name, "--version", str(version)],
            capture_output=True, text=True, timeout=600)
        if proc.returncode != 0:
            return {"status": "echec-processus", "rows": 0, "max_abs_delta": None, "stderr": proc.stderr[-500:]}
        return json.loads(proc.stdout.strip().splitlines()[-1])
    return run


def release_model(cfg: dict, run_dir, backend: RegistryBackend, *, reload_verifier=None) -> dict:
    """Enregistre la version, contrôle le rechargement, qualifie. Retourne un résumé."""
    run_dir = Path(run_dir)
    tracking = json.loads((run_dir / "tracking.json").read_text(encoding="utf-8"))
    evidence = json.loads((run_dir / "evidence.json").read_text(encoding="utf-8"))
    name = cfg["tracking"]["model_name"]
    pipeline_run = run_dir.name

    version = backend.find_version_by_pipeline_run(name, pipeline_run)
    if version is None:
        version = backend.register(
            tracking["model_uri"], name, tracking["mlflow_run_id"],
            {"pipeline.run": pipeline_run, "pipeline.mlflow_run_id": tracking["mlflow_run_id"],
             "data.origin": evidence["data_origin"], "qualification": "pending",
             "data.snapshot_sha256": evidence["snapshot_sha256"]},
            evidence=evidence)
    verifier = reload_verifier or subprocess_reload_verifier(cfg)
    check = verifier(run_dir, name, version)
    backend.set_tags(name, version, {"reload.check": json.dumps(check)})

    expected = read_store_manifest(cfg)["files"]["qualification.csv"]["sha256"]
    decision: Decision = qualify_version(backend, name, version, load_policy(cfg), expected,
                                         report_dir=path_of(cfg, "reports_dir"))
    return {"model": name, "version": version, "verdict": decision.verdict, "reasons": decision.reasons,
            "reload": check}
