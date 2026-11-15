"""Récupération reproductible des données brutes (A2).

`fetch` télécharge l'archive UCI, extrait hour.csv et écrit un manifeste (SHA-256, licence, date).
Au premier téléchargement, le manifeste est créé : il faut le VERSIONNER (data/manifest.json).
Ensuite, chaque `fetch` vérifie l'empreinte : un fichier différent est refusé.
"""
from __future__ import annotations

import io
import json
import time
import zipfile
from pathlib import Path

import pandas as pd
import requests

from .config import path_of
from .contract import RAW_COLUMNS
from .errors import ConfigError, DataIntegrityError, TransientError
from .state import now_utc, sha256_bytes, sha256_file, write_json_atomic

TRANSIENT_STATUS = {429, 500, 502, 503, 504}


def extract_member(zip_bytes: bytes, member: str) -> bytes:
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = [n for n in zf.namelist() if n.endswith(member)]
        if not names:
            raise ConfigError(f"{member} absent de l'archive (contenu : {zf.namelist()})")
        return zf.read(names[0])


def download(url: str, *, opener=requests.get, sleep=time.sleep, attempts: int = 3, timeout: int = 60) -> bytes:
    """Télécharge avec 3 tentatives MAXIMUM, uniquement sur erreur réseau, 429 ou 5xx.
    Un 404 ou une autre erreur permanente est remontée tout de suite (la rejouer ne sert à rien)."""
    last = None
    for attempt in range(1, attempts + 1):
        try:
            resp = opener(url, timeout=timeout)
        except (requests.ConnectionError, requests.Timeout) as exc:
            last = exc
        else:
            if resp.status_code in TRANSIENT_STATUS:
                last = TransientError(f"HTTP {resp.status_code}")
            else:
                resp.raise_for_status()
                return resp.content
        if attempt < attempts:
            sleep(min(2 ** attempt, 8))
    raise TransientError(f"Téléchargement impossible après {attempts} tentatives : {last}")


def install_raw(cfg: dict, csv_bytes: bytes, *, origin: str = "uci") -> dict:
    """Écrit le CSV brut et le manifeste, ou vérifie l'empreinte si le manifeste existe déjà."""
    member = cfg["source"]["member"]
    manifest_path = path_of(cfg, "manifest")
    digest = sha256_bytes(csv_bytes)
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest["sha256"] != digest:
            raise DataIntegrityError(
                f"Empreinte inattendue pour {member} : {digest[:12]}… au lieu de {manifest['sha256'][:12]}… "
                "(le fichier téléchargé n'est pas celui du manifeste versionné)."
            )
    else:
        frame = pd.read_csv(io.BytesIO(csv_bytes))
        missing = [c for c in RAW_COLUMNS if c not in frame.columns]
        if missing:
            raise DataIntegrityError(f"Colonnes attendues absentes de {member} : {missing}")
        src = cfg["source"]
        manifest = {
            "source": {k: src[k] for k in ("name", "url", "member", "licence", "doi", "citation")},
            "origin": origin,
            "retrieved_at_utc": now_utc(),
            "file": member,
            "sha256": digest,
            "bytes": len(csv_bytes),
            "rows": int(len(frame)),
            "columns": list(frame.columns),
        }
        write_json_atomic(manifest_path, manifest)
    raw_path = path_of(cfg, "raw_dir") / member
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_bytes(csv_bytes)
    return manifest


def fetch(cfg: dict, *, from_file=None, opener=requests.get, sleep=time.sleep) -> dict:
    """Obtient hour.csv : depuis un fichier local (zip ou csv) ou en téléchargeant l'archive UCI."""
    member = cfg["source"]["member"]
    if from_file:
        data = Path(from_file).read_bytes()
        if zipfile.is_zipfile(io.BytesIO(data)):
            data = extract_member(data, member)
    else:
        data = extract_member(download(cfg["source"]["url"], opener=opener, sleep=sleep), member)
    return install_raw(cfg, data, origin="uci")


def verify_raw(cfg: dict) -> tuple[pd.DataFrame, dict]:
    """Relit le CSV brut APRÈS vérification de son empreinte."""
    manifest_path = path_of(cfg, "manifest")
    if not manifest_path.is_file():
        raise ConfigError("Manifeste absent : lancez d'abord `velos fetch`.")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    raw_path = path_of(cfg, "raw_dir") / manifest["file"]
    if not raw_path.is_file():
        raise ConfigError(f"Fichier brut absent : {raw_path} (lancez `velos fetch`).")
    if sha256_file(raw_path) != manifest["sha256"]:
        raise DataIntegrityError(f"{raw_path.name} ne correspond plus à l'empreinte du manifeste.")
    return pd.read_csv(raw_path), manifest
