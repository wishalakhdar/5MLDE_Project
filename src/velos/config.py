"""Chargement de la configuration. Aucun réglage n'est écrit en dur dans le code."""
from __future__ import annotations

import json
from pathlib import Path

from .errors import ConfigError

REQUIRED_KEYS = [
    "project", "seed", "paths", "source", "replay", "quality", "split",
    "model", "tracking", "qualification", "alerts", "retry",
]


def load_config(path) -> dict:
    """Lit le JSON. Les chemins relatifs sont résolus depuis le dossier du fichier de config,
    jamais depuis le dossier courant : la même commande marche de partout."""
    p = Path(path).resolve()
    if not p.is_file():
        raise ConfigError(f"Fichier de configuration introuvable : {p}")
    try:
        cfg = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Configuration illisible ({p}) : {exc}") from exc
    missing = [k for k in REQUIRED_KEYS if k not in cfg]
    if missing:
        raise ConfigError(f"Clés absentes de la configuration : {missing}")
    cfg["_base_dir"] = str(p.parent)
    cfg["_config_path"] = str(p)
    return cfg


def resolve(cfg: dict, relative) -> Path:
    base = Path(cfg["_base_dir"])
    p = Path(relative)
    return p if p.is_absolute() else (base / p).resolve()


def path_of(cfg: dict, key: str) -> Path:
    try:
        return resolve(cfg, cfg["paths"][key])
    except KeyError as exc:
        raise ConfigError(f"Chemin inconnu dans la configuration : paths.{key}") from exc
