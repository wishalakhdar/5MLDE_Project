"""Suivi des exécutions. Une interface, deux implémentations :
  - NullTracker   : enregistre en mémoire (tests, mode sans MLflow) ;
  - MlflowTracker : écrit dans MLflow (adaptateur fin, à valider sur votre machine avec le serveur lancé).

Suivi EXPLICITE : le run_id est transmis aux étapes via l'objet tracker, jamais via un run « actif » global.
"""
from __future__ import annotations

import os
import subprocess
import time
import urllib.error
import urllib.request

from .errors import TransientError


def git_info(cwd=None) -> dict:
    """Révision du code et état du dépôt (« unknown » si git est absent)."""
    def run(*args):
        try:
            return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=10, check=True).stdout.strip()
        except Exception:
            return None
    commit = run("rev-parse", "HEAD")
    dirty = run("status", "--porcelain")
    return {"git.commit": commit or "unknown", "git.dirty": "unknown" if commit is None else str(bool(dirty)).lower()}


class Tracker:
    run_id: str | None = None

    def start(self, run_name: str, tags: dict) -> None: ...
    def set_tags(self, tags: dict) -> None: ...
    def log_params(self, params: dict) -> None: ...
    def log_metrics(self, metrics: dict) -> None: ...
    def log_artifacts(self, directory) -> None: ...
    def log_model(self, pipeline, name: str, input_example) -> str: ...
    def end(self) -> None: ...
    def fail(self, stage: str, exc: BaseException) -> None: ...


class NullTracker(Tracker):
    def __init__(self):
        self.run_id = "null-run"
        self.events: list[tuple] = []
        self.status = "NEW"

    def start(self, run_name, tags):
        self.status = "RUNNING"
        self.events.append(("start", run_name, dict(tags)))

    def set_tags(self, tags):
        self.events.append(("tags", dict(tags)))

    def log_params(self, params):
        self.events.append(("params", dict(params)))

    def log_metrics(self, metrics):
        self.events.append(("metrics", dict(metrics)))

    def log_artifacts(self, directory):
        self.events.append(("artifacts", str(directory)))

    def log_model(self, pipeline, name, input_example):
        self.events.append(("model", name))
        return f"null://{name}"

    def end(self):
        self.status = "FINISHED"

    def fail(self, stage, exc):
        self.status = "FAILED"
        self.events.append(("fail", stage, f"{type(exc).__name__}: {exc}"))


def check_server(uri: str, attempts: int = 3, sleep=time.sleep) -> None:
    """Vérifie que MLflow répond. Sinon : erreur explicite (aucun run, aucun modèle publié)."""
    last = None
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(uri.rstrip("/") + "/health", timeout=5) as resp:
                if resp.status == 200:
                    return
        except (urllib.error.URLError, OSError) as exc:
            last = exc
        if attempt < attempts:
            sleep(2)
    raise TransientError(f"Serveur MLflow injoignable ({uri}) : {last}. Lancez `docker compose -f infra/compose.yml up -d`.")


class MlflowTracker(Tracker):
    def __init__(self, uri: str, experiment: str):
        import mlflow
        from mlflow import MlflowClient
        self.mlflow = mlflow
        self.uri = uri
        self.experiment = experiment
        mlflow.set_tracking_uri(uri)
        mlflow.set_registry_uri(uri)
        self.client = MlflowClient(tracking_uri=uri, registry_uri=uri)

    def start(self, run_name, tags):
        check_server(self.uri)
        exp = self.client.get_experiment_by_name(self.experiment)
        exp_id = exp.experiment_id if exp else self.client.create_experiment(self.experiment)
        run = self.client.create_run(exp_id, tags={**tags, **git_info()}, run_name=run_name)
        self.run_id = run.info.run_id

    def set_tags(self, tags):
        for k, v in tags.items():
            self.client.set_tag(self.run_id, k, str(v)[:5000])

    def log_params(self, params):
        for k, v in params.items():
            self.client.log_param(self.run_id, k, str(v)[:500])

    def log_metrics(self, metrics):
        for k, v in metrics.items():
            self.client.log_metric(self.run_id, k, float(v))

    def log_artifacts(self, directory):
        self.client.log_artifacts(self.run_id, str(directory))

    def log_model(self, pipeline, name, input_example):
        from mlflow.models import infer_signature
        signature = infer_signature(input_example, pipeline.predict(input_example))
        with self.mlflow.start_run(run_id=self.run_id):
            info = self.mlflow.sklearn.log_model(
                pipeline, name=name, signature=signature, input_example=input_example.head(5),
                serialization_format="cloudpickle",
            )
        return info.model_uri

    def end(self):
        self.client.set_terminated(self.run_id, "FINISHED")

    def fail(self, stage, exc):
        if self.run_id is None:
            return
        try:
            self.client.set_tag(self.run_id, "failure.stage", stage)
            self.client.set_tag(self.run_id, "failure.error", f"{type(exc).__name__}: {exc}"[:4000])
            self.client.set_terminated(self.run_id, "FAILED")
        except Exception:
            pass  # ne jamais masquer l'erreur d'origine


def make_tracker(cfg: dict) -> Tracker:
    t = cfg["tracking"]
    if not t.get("enabled", True):
        return NullTracker()
    uri = os.environ.get(t["uri_env"], t["default_uri"])
    return MlflowTracker(uri, t["experiment"])
