"""Orchestration Prefect (A6). Nécessite `prefect` : non exécuté dans l'environnement de développement initial.

  process_lot_flow   : un lot (ou la base) -> 4 étapes -> release (registre) -> acceptation du lot
  watch_incoming_flow: surveille data/incoming et traite chaque lot dans l'ordre
Les échecs déclenchent une alerte ; seuls les incidents TRANSITOIRES sont rejoués (alerts.is_transient).
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from prefect import flow, get_run_logger, task

from .alerts import is_transient, send_alert
from .config import load_config, path_of
from .errors import AlreadyProcessed, QualityError
from .evaluation import evaluate_models
from .pipeline import commit_lot, load_data
from .preprocessing import preprocess_data
from .registry import make_backend
from .release import release_model
from .run_log import make_entry, record_pipeline_run
from .tracking import make_tracker
from .training import train_models


def retry_only_transient(task_obj, task_run, state) -> bool:
    """Condition de nouvelle tentative Prefect : on ne rejoue que les pannes passagères."""
    try:
        state.result()
    except BaseException as exc:  # noqa: BLE001
        return is_transient(exc)
    return False


def _retries(cfg: dict) -> dict:
    return {"retries": cfg["retry"]["max_retries"], "retry_delay_seconds": cfg["retry"]["delay_seconds"],
            "retry_condition_fn": retry_only_transient}


def alert_on_failure(flow_obj, flow_run, state) -> None:
    send_alert(f"[velos] ÉCHEC du flow « {flow_run.name} » : {state.message}")


@task(name="1-ingestion-qualite")
def t_load(cfg, lot_path, tracker):
    return load_data(cfg, lot_path, tracker)


@task(name="2-pretraitement")
def t_prep(data):
    return preprocess_data(data)


@task(name="3-entrainement")
def t_train(prepared):
    return train_models(prepared)


@task(name="4-evaluation")
def t_eval(trained):
    return evaluate_models(trained)


@task(name="5-release-registre")
def t_release(cfg, result):
    return release_model(cfg, result.run_dir, make_backend(cfg))


@task(name="6-acceptation-lot")
def t_commit(cfg, result):
    return commit_lot(cfg, result)


@flow(name="velos-process-lot", on_failure=[alert_on_failure], on_crashed=[alert_on_failure])
def process_lot_flow(config_path: str, lot_path: str | None = None) -> dict:
    log = get_run_logger()
    cfg = load_config(config_path)
    ret = _retries(cfg)
    tracker = make_tracker(cfg)
    # Journal du traitement pour Grafana (tracing.pipeline_runs), écrit dans TOUS les cas (finally).
    started_at = datetime.now(timezone.utc)
    outcome = {"status": "failed"}
    try:
        # Ingestion/qualité : une erreur de qualité est PERMANENTE (jamais rejouée)
        data = t_load.with_options(**ret)(cfg, lot_path, tracker)
        prepared = t_prep(data)
        trained = t_train(prepared)
        result = t_eval(trained)
        release = t_release.with_options(**ret)(cfg, result)
        t_commit(cfg, result)
        outcome = {"status": "succeeded", "model_version": release["version"], "verdict": release["verdict"]}
    except AlreadyProcessed as exc:
        outcome = {"status": "already_processed"}
        log.info("Rien à faire : %s", exc)
        return {"status": "deja-traite"}
    except QualityError as exc:
        outcome = {"status": "rejected", "error": str(exc)}
        tracker.fail("ingestion", exc)
        raise
    except BaseException as exc:
        outcome = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
        tracker.fail("flow", exc)
        raise
    finally:
        lot_id = Path(lot_path).stem if lot_path else "base"
        record_pipeline_run(make_entry("velos-process-lot", lot_id, started_at, datetime.now(timezone.utc),
                                       **outcome))
    log.info("Version %s — verdict %s", release["version"], release["verdict"])
    return {"status": "ok", **release}


@flow(name="velos-watch-incoming")
def watch_incoming_flow(config_path: str) -> list:
    cfg = load_config(config_path)
    incoming = path_of(cfg, "incoming_dir")
    done = []
    for lot in sorted(Path(incoming).glob("lot-*.csv")):
        try:
            process_lot_flow(config_path, str(lot))
            done.append(lot.name)
        except QualityError:
            continue  # lot en quarantaine, alerte déjà envoyée : on passe au suivant
    return done


def serve_flows(config_path: str) -> None:
    cfg = load_config(config_path)
    watch_incoming_flow.serve(name="velos-watch", parameters={"config_path": config_path},
                              interval=cfg["schedule"]["interval_seconds"])
