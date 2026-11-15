"""État persistant : registre des lots acceptés (ledger). Écritures atomiques."""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from .config import path_of
from .errors import DataIntegrityError


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def frame_sha256(df: pd.DataFrame) -> str:
    """Empreinte déterministe d'un DataFrame (via sa sérialisation CSV)."""
    return sha256_bytes(df.to_csv(index=False, lineterminator="\n").encode("utf-8"))


def write_json_atomic(path, obj) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    os.replace(tmp, p)
    return p


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---- manifeste du magasin (fichiers dérivés par le rejeu) ----
def store_manifest_path(cfg) -> Path:
    return path_of(cfg, "store_dir") / "store-manifest.json"


def read_store_manifest(cfg) -> dict:
    p = store_manifest_path(cfg)
    if not p.is_file():
        from .errors import ConfigError
        raise ConfigError("Magasin de données absent : lancez d'abord `velos replay-init`.")
    return json.loads(p.read_text(encoding="utf-8"))


def verified_store_file(cfg, relative: str) -> Path:
    """Chemin d'un fichier du magasin APRÈS vérification de son empreinte."""
    manifest = read_store_manifest(cfg)
    path = path_of(cfg, "store_dir") / relative
    expected = manifest["files"].get(relative, {}).get("sha256")
    if not path.is_file() or expected is None:
        raise DataIntegrityError(f"Fichier du magasin introuvable ou inconnu : {relative}")
    if sha256_file(path) != expected:
        raise DataIntegrityError(f"Empreinte SHA-256 différente pour {relative} : le fichier a été modifié.")
    return path


# ---- ledger des lots acceptés ----
def ledger_path(cfg) -> Path:
    return path_of(cfg, "state_dir") / "ledger.json"


def read_ledger(cfg) -> dict:
    p = ledger_path(cfg)
    if not p.is_file():
        return {"accepted": []}
    return json.loads(p.read_text(encoding="utf-8"))


def accepted_ids(cfg) -> list[str]:
    return [e["lot_id"] for e in read_ledger(cfg)["accepted"]]


def append_accepted(cfg, entry: dict) -> None:
    ledger = read_ledger(cfg)
    if entry["lot_id"] in [e["lot_id"] for e in ledger["accepted"]]:
        return
    ledger["accepted"].append(entry)
    write_json_atomic(ledger_path(cfg), ledger)


def load_snapshot(cfg) -> pd.DataFrame:
    """Snapshot courant = base + lots acceptés (chaque fichier est vérifié par son empreinte)."""
    frames = [pd.read_csv(verified_store_file(cfg, "base.csv"))]
    for entry in read_ledger(cfg)["accepted"]:
        path = path_of(cfg, "state_dir") / "accepted" / entry["file"]
        if not path.is_file() or sha256_file(path) != entry["sha256"]:
            raise DataIntegrityError(f"Lot accepté modifié ou introuvable : {entry['lot_id']}")
        frames.append(pd.read_csv(path))
    return pd.concat(frames, ignore_index=True).sort_values("instant").reset_index(drop=True)
