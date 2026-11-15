"""Rejeu du dataset historique par lots, pour SIMULER l'arrivée de nouvelles données.

Précision à reprendre dans le README et la synthèse : UCI Bike Sharing est un jeu HISTORIQUE (2011-2012).
Il est découpé ainsi :
  - base.csv            : l'année de base (2011), point de départ de l'entraînement ;
  - lots/lot-AAAA-MM.csv: un lot = un mois de 2012, « arrivé » un par un ;
  - qualification.csv   : un mois FIXE (2012-12) jamais utilisé pour apprendre, commun à tous les candidats.
"""
from __future__ import annotations

import shutil

import pandas as pd

from .config import path_of
from .errors import ConfigError, DataIntegrityError
from .ingestion import verify_raw
from .state import accepted_ids, now_utc, read_store_manifest, sha256_file, store_manifest_path, write_json_atomic


def _write(df: pd.DataFrame, path) -> dict:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, lineterminator="\n")
    return {"sha256": sha256_file(path), "rows": int(len(df))}


def init_store(cfg: dict) -> dict:
    """Construit le magasin dérivé. Idempotent : même données brutes = même fichiers (mêmes empreintes)."""
    raw, raw_manifest = verify_raw(cfg)
    store = path_of(cfg, "store_dir")
    mpath = store_manifest_path(cfg)
    if mpath.is_file():
        existing = read_store_manifest(cfg)
        if existing["raw_sha256"] == raw_manifest["sha256"]:
            return existing
        raise DataIntegrityError("Le magasin existe déjà avec d'autres données brutes : supprimez le dossier "
                                 f"{store} (et data/state) pour repartir de zéro.")

    raw = raw.sort_values("instant").reset_index(drop=True)
    dates = pd.to_datetime(raw["dteday"], format="%Y-%m-%d")
    month = dates.dt.strftime("%Y-%m")
    rp = cfg["replay"]
    is_base = dates.dt.year == rp["base_year"]
    is_corpus = month == rp["qualification_month"]
    rest = raw[~is_base & ~is_corpus]
    if is_base.sum() == 0 or is_corpus.sum() == 0 or rest.empty:
        raise ConfigError("Découpage impossible : année de base, mois de qualification ou lots vides.")

    files = {"base.csv": _write(raw[is_base], store / "base.csv"),
             "qualification.csv": _write(raw[is_corpus], store / "qualification.csv")}
    for mid, group in rest.groupby(month[rest.index], sort=True):
        files[f"lots/lot-{mid}.csv"] = _write(group, store / "lots" / f"lot-{mid}.csv")
    manifest = {"created_at_utc": now_utc(), "raw_sha256": raw_manifest["sha256"],
                "origin": raw_manifest.get("origin", "uci"), "base_year": rp["base_year"],
                "qualification_month": rp["qualification_month"], "files": files}
    write_json_atomic(mpath, manifest)
    return manifest


def pending_lots(cfg: dict) -> list[str]:
    done = set(accepted_ids(cfg))
    names = sorted(k.split("/")[1][:-4] for k in read_store_manifest(cfg)["files"] if k.startswith("lots/"))
    return [n for n in names if n not in done]


def next_lot(cfg: dict):
    """Fait « arriver » le prochain lot : le copie dans data/incoming. Retourne son chemin (ou None)."""
    todo = pending_lots(cfg)
    if not todo:
        return None
    lot_id = todo[0]
    target = path_of(cfg, "incoming_dir") / f"{lot_id}.csv"
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        shutil.copyfile(path_of(cfg, "store_dir") / "lots" / f"{lot_id}.csv", target)
    return target


def make_bad_lot(cfg: dict, kind: str = "negatif"):
    """INCIDENT n°1 : fabrique une version INVALIDE du prochain lot et la dépose dans data/incoming."""
    todo = pending_lots(cfg)
    if not todo:
        return None
    lot_id = todo[0]
    df = pd.read_csv(path_of(cfg, "store_dir") / "lots" / f"{lot_id}.csv")
    if kind == "negatif":
        df.loc[df.index[:5], "cnt"] = -10          # comptes négatifs
        df.loc[df.index[5:8], "hr"] = 25           # heure impossible
    elif kind == "colonne":
        df = df.drop(columns=["hum"])              # colonne manquante
    elif kind == "volume":
        df = df.head(50)                           # lot quasi vide
    else:
        raise ConfigError("kind inconnu : negatif, colonne ou volume")
    target = path_of(cfg, "incoming_dir") / f"{lot_id}.csv"
    target.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(target, index=False, lineterminator="\n")
    return target
