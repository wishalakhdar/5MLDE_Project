"""Les 4 étapes du pipeline et leur enchaînement.

    load_data -> preprocess_data -> train_models -> evaluate_models   (puis release + commit_lot)

`load_data` (cette page) lit, VALIDE (barrière bloquante) et prépare le dossier du run.
Une anomalie de qualité arrête TOUT : ni prétraitement, ni entraînement, ni modèle publié.
"""
from __future__ import annotations

import shutil
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from .config import path_of
from .errors import AlreadyProcessed, ConfigError, DataIntegrityError, QualityError
from .evaluation import evaluate_models, is_complete
from .preprocessing import preprocess_data
from .quality import enforce, validate
from .stages import EvaluationResult, IngestedData
from .state import (append_accepted, frame_sha256, load_snapshot, now_utc, read_ledger, read_store_manifest,
                    sha256_file, verified_store_file, write_json_atomic)
from .tracking import NullTracker
from .training import train_models


@contextmanager
def guarded(stage: str, tracker):
    """Ferme le run en FAILED avec l'étape en cause, puis laisse l'erreur d'origine remonter."""
    try:
        yield
    except BaseException as exc:
        tracker.fail(stage, exc)
        raise


def _quarantine(cfg: dict, lot_path: Path, lot_id: str, report) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    rejected = path_of(cfg, "rejected_dir")
    rejected.mkdir(parents=True, exist_ok=True)
    report.write(rejected / f"{lot_id}.{stamp}.rapport.json")
    target = rejected / f"{lot_id}.{stamp}.csv"
    shutil.move(str(lot_path), target)
    return target


def load_data(cfg: dict, lot_path=None, tracker=None) -> IngestedData:
    tracker = tracker or NullTracker()
    lot_path = Path(lot_path) if lot_path else None
    if lot_path is not None and not lot_path.is_file():
        raise ConfigError(f"Lot introuvable : {lot_path}")
    lot_id = lot_path.stem if lot_path else None
    store_manifest = read_store_manifest(cfg)
    origin = store_manifest.get("origin", "uci")
    ledger = read_ledger(cfg)
    reports: list[dict] = []

    # 0. Idempotence : un lot déjà accepté n'est pas retraité (et ne crée aucun run de suivi)
    if lot_id:
        known = next((e for e in ledger["accepted"] if e["lot_id"] == lot_id), None)
        if known:
            if sha256_file(lot_path) != known["sha256"]:
                raise DataIntegrityError(f"{lot_id} a déjà été accepté avec un contenu différent.")
            raise AlreadyProcessed(f"{lot_id} déjà accepté : rien à faire.")

    tracker.start(run_name=f"{lot_id or 'base'}-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}",
                  tags={"lot.id": lot_id or "base", "data.origin": origin, "stage": "pipeline"})

    # 1. Snapshot actuel (base + lots acceptés), vérifié puis validé
    before = load_snapshot(cfg)
    reports.append(enforce(validate(before, "snapshot", cfg, source="snapshot actuel",
                                    sha256=frame_sha256(before))).to_dict())

    # 2. Lot entrant : validé AVANT toute fusion. En cas d'échec : quarantaine + rapport + exception.
    lot_df, lot_sha, snapshot = None, None, before
    if lot_path is not None:
        lot_df = pd.read_csv(lot_path)
        lot_sha = sha256_file(lot_path)
        report = validate(lot_df, "lot", cfg, reference=before, lot_name=lot_id, source=str(lot_path), sha256=lot_sha)
        if not report.passed:
            _quarantine(cfg, lot_path, lot_id, report)
            raise QualityError(report)
        reports.append(report.to_dict())
        snapshot = pd.concat([before, lot_df], ignore_index=True).sort_values("instant").reset_index(drop=True)
        reports.append(enforce(validate(snapshot, "snapshot", cfg, source="snapshot après fusion",
                                        sha256=frame_sha256(snapshot))).to_dict())

    # 3. Corpus de qualification : empreinte vérifiée puis validé
    corpus_path = verified_store_file(cfg, "qualification.csv")
    corpus = pd.read_csv(corpus_path)
    reports.append(enforce(validate(corpus, "corpus", cfg, source=str(corpus_path),
                                    sha256=sha256_file(corpus_path))).to_dict())

    snapshot_sha = frame_sha256(snapshot)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    run_dir = path_of(cfg, "runs_dir") / f"run-{stamp}-{lot_id or 'base'}-{snapshot_sha[:8]}"
    if run_dir.exists():
        raise DataIntegrityError(f"Dossier de run déjà existant (refusé pour ne rien écraser) : {run_dir}")

    provenance = {
        "origin": origin, "source": cfg["source"], "raw_sha256": store_manifest["raw_sha256"],
        "snapshot_sha256": snapshot_sha, "snapshot_rows": int(len(snapshot)),
        "corpus_sha256": sha256_file(corpus_path),
        "components": [{"file": "base.csv", "sha256": store_manifest["files"]["base.csv"]["sha256"]}]
                      + [{"file": e["file"], "sha256": e["sha256"]} for e in ledger["accepted"]]
                      + ([{"file": f"{lot_id}.csv", "sha256": lot_sha}] if lot_id else []),
        "lot_id": lot_id, "created_at_utc": now_utc(),
    }
    tracker.set_tags({"data.snapshot_sha256": snapshot_sha, "data.corpus_sha256": provenance["corpus_sha256"],
                      "data.rows": len(snapshot)})
    return IngestedData(cfg=cfg, snapshot=snapshot, corpus=corpus, lot=lot_df, lot_id=lot_id, lot_path=lot_path,
                        lot_sha256=lot_sha, provenance=provenance, run_dir=run_dir, tracker=tracker,
                        quality_reports=reports)


def run_pipeline(cfg: dict, lot_path=None, tracker=None) -> EvaluationResult:
    """Les 4 étapes, avec fermeture propre du run de suivi en cas d'échec."""
    tracker = tracker or NullTracker()
    stage = "ingestion"
    try:
        data = load_data(cfg, lot_path, tracker)
        stage = "preprocessing"
        prepared = preprocess_data(data)
        stage = "training"
        trained = train_models(prepared)
        stage = "evaluation"
        return evaluate_models(trained)
    except AlreadyProcessed:
        raise  # pas un échec : rien à faire
    except BaseException as exc:
        tracker.fail(stage, exc)
        raise


def commit_lot(cfg: dict, result: EvaluationResult) -> None:
    """DERNIÈRE étape : le lot n'est « accepté » que si le run est complet (publication.json valide).
    Termine aussi le run de suivi (FINISHED)."""
    data = result.ingested
    if not is_complete(result.run_dir):
        raise DataIntegrityError(f"Run incomplet, lot non accepté : {result.run_dir}")
    if data.lot_id is not None:
        accepted_dir = path_of(cfg, "state_dir") / "accepted"
        accepted_dir.mkdir(parents=True, exist_ok=True)
        target = accepted_dir / data.lot_path.name
        shutil.copyfile(data.lot_path, target)
        append_accepted(cfg, {"lot_id": data.lot_id, "file": target.name, "sha256": data.lot_sha256,
                              "rows": int(len(data.lot)), "accepted_at_utc": now_utc(),
                              "run_dir": result.run_dir.name})
        data.lot_path.unlink(missing_ok=True)
    data.tracker.end()
