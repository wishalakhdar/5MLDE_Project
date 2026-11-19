"""Accès au registre de modèles. Une interface, deux implémentations :
  - InMemoryRegistry : tests et essais à blanc ;
  - MlflowRegistry   : adaptateur fin vers le Model Registry MLflow (validé avec un serveur MLflow 3.16.1 réel).

La LOGIQUE (qualification, sélection, retour arrière) vit dans lifecycle.py et est testée avec InMemoryRegistry.
"""
from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod

from .errors import RegistryError


class RegistryBackend(ABC):
    @abstractmethod
    def register(self, model_uri: str, name: str, run_id: str, tags: dict, evidence: dict | None = None) -> int: ...
    @abstractmethod
    def find_version_by_pipeline_run(self, name: str, pipeline_run: str) -> int | None: ...
    @abstractmethod
    def set_tags(self, name: str, version: int, tags: dict) -> None: ...
    @abstractmethod
    def get_tags(self, name: str, version: int) -> dict: ...
    @abstractmethod
    def get_evidence(self, name: str, version: int) -> dict: ...
    @abstractmethod
    def set_alias(self, name: str, alias: str, version: int) -> None: ...
    @abstractmethod
    def get_alias(self, name: str, alias: str) -> int | None: ...
    @abstractmethod
    def version_exists(self, name: str, version: int) -> bool: ...
    @abstractmethod
    def list_versions(self, name: str) -> list: ...


class InMemoryRegistry(RegistryBackend):
    def __init__(self):
        self.versions: dict[tuple[str, int], dict] = {}
        self.aliases: dict[tuple[str, str], int] = {}

    def register(self, model_uri, name, run_id, tags, evidence=None):
        version = 1 + max([v for (n, v) in self.versions if n == name], default=0)
        self.versions[(name, version)] = {"uri": model_uri, "run_id": run_id, "tags": dict(tags),
                                           "evidence": evidence or {}}
        return version

    def find_version_by_pipeline_run(self, name, pipeline_run):
        for (n, v), info in self.versions.items():
            if n == name and info["tags"].get("pipeline.run") == pipeline_run:
                return v
        return None

    def set_tags(self, name, version, tags):
        self._get(name, version)["tags"].update({k: str(v) for k, v in tags.items()})

    def get_tags(self, name, version):
        return dict(self._get(name, version)["tags"])

    def get_evidence(self, name, version):
        return self._get(name, version)["evidence"]

    def set_alias(self, name, alias, version):
        self._get(name, version)
        self.aliases[(name, alias)] = version

    def get_alias(self, name, alias):
        return self.aliases.get((name, alias))

    def version_exists(self, name, version):
        return (name, version) in self.versions

    def list_versions(self, name):
        return sorted(v for (n, v) in self.versions if n == name)

    def _get(self, name, version):
        try:
            return self.versions[(name, version)]
        except KeyError as exc:
            raise RegistryError(f"Version inconnue : {name} v{version}") from exc


class MlflowRegistry(RegistryBackend):
    def __init__(self, uri: str):
        import mlflow
        from mlflow import MlflowClient
        mlflow.set_tracking_uri(uri)
        mlflow.set_registry_uri(uri)
        self.mlflow = mlflow
        self.client = MlflowClient(tracking_uri=uri, registry_uri=uri)

    def register(self, model_uri, name, run_id, tags, evidence=None):
        # `evidence` est ignoré ici : MLflow relit les preuves depuis l'artefact du run (voir get_evidence).
        mv = self.mlflow.register_model(model_uri=model_uri, name=name, tags={k: str(v) for k, v in tags.items()})
        return int(mv.version)

    def find_version_by_pipeline_run(self, name, pipeline_run):
        for mv in self.client.search_model_versions(f"name='{name}'"):
            if (mv.tags or {}).get("pipeline.run") == pipeline_run:
                return int(mv.version)
        return None

    def set_tags(self, name, version, tags):
        for k, v in tags.items():
            self.client.set_model_version_tag(name, str(version), k, str(v)[:5000])

    def get_tags(self, name, version):
        return dict(self.client.get_model_version(name, str(version)).tags or {})

    def get_evidence(self, name, version):
        """Les preuves sont relues depuis l'ARTEFACT du run d'origine (pas depuis un tag modifiable à la main)."""
        mv = self.client.get_model_version(name, str(version))
        run_id = self.get_tags(name, version).get("pipeline.mlflow_run_id") or mv.run_id
        if not run_id:
            raise RegistryError(f"Aucun run associé à {name} v{version}")
        path = self.mlflow.artifacts.download_artifacts(run_id=run_id, artifact_path="evidence.json")
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)

    def set_alias(self, name, alias, version):
        self.client.set_registered_model_alias(name, alias, str(version))

    def get_alias(self, name, alias):
        try:
            return int(self.client.get_model_version_by_alias(name, alias).version)
        except Exception:
            return None

    def list_versions(self, name):
        return sorted(int(mv.version) for mv in self.client.search_model_versions(f"name='{name}'"))

    def version_exists(self, name, version):
        try:
            self.client.get_model_version(name, str(version))
            return True
        except Exception:
            return False


def make_backend(cfg: dict) -> RegistryBackend:
    t = cfg["tracking"]
    return MlflowRegistry(os.environ.get(t["uri_env"], t["default_uri"]))
