"""Une règle de qualité = un test : chaque défaut injecté doit être détecté par SA règle."""
import json

import pandas as pd
import pytest

from velos.config import path_of
from velos.errors import QualityError
from velos.quality import enforce, validate


@pytest.fixture
def base(cfg):
    return pd.read_csv(path_of(cfg, "store_dir") / "base.csv")


@pytest.fixture
def lot(cfg):
    return pd.read_csv(path_of(cfg, "store_dir") / "lots" / "lot-2012-01.csv")


def rules_failed(report):
    return {f.rule for f in report.failures}


def check(cfg, lot, base, df, name="lot-2012-01"):
    return validate(df, "lot", cfg, reference=base, lot_name=name)


def test_valid_lot_passes(cfg, lot, base):
    assert check(cfg, lot, base, lot).passed


def test_structure(cfg, lot, base):
    assert "structure" in rules_failed(check(cfg, lot, base, lot.drop(columns=["hum"])))


def test_valeurs_manquantes(cfg, lot, base):
    df = lot.copy(); df.loc[df.index[0], "temp"] = None
    assert "valeurs_manquantes" in rules_failed(check(cfg, lot, base, df))


def test_types(cfg, lot, base):
    df = lot.copy(); df["temp"] = df["temp"].astype(object); df.loc[df.index[0], "temp"] = "abc"
    assert "types" in rules_failed(check(cfg, lot, base, df))


def test_plages(cfg, lot, base):
    df = lot.copy(); df.loc[df.index[0], "hr"] = 25
    assert "plages" in rules_failed(check(cfg, lot, base, df))


def test_coherence_cnt(cfg, lot, base):
    df = lot.copy(); df.loc[df.index[0], "cnt"] = df.loc[df.index[0], "cnt"] + 7
    assert "coherence_cnt" in rules_failed(check(cfg, lot, base, df))


def test_unicite(cfg, lot, base):
    df = pd.concat([lot, lot.head(1)], ignore_index=True)
    assert "unicite" in rules_failed(check(cfg, lot, base, df))


def test_coherence_dates(cfg, lot, base):
    df = lot.copy(); df.loc[df.index[0], "yr"] = 0   # le lot est en 2012 => yr doit valoir 1
    assert "coherence_dates" in rules_failed(check(cfg, lot, base, df))


def test_volume(cfg, lot, base):
    assert "volume" in rules_failed(check(cfg, lot, base, lot.head(50)))


def test_representativite_heures(cfg, lot, base):
    assert "representativite" in rules_failed(check(cfg, lot, base, lot[lot["hr"] < 12]))


def test_representativite_moyenne(cfg, lot, base):
    df = lot.copy()
    for c in ("cnt", "casual", "registered"):
        df[c] = df[c] * 10
    assert "representativite" in rules_failed(check(cfg, lot, base, df))


def test_continuite_nom(cfg, lot, base):
    assert "continuite_lot" in rules_failed(check(cfg, lot, base, lot, name="lot-2012-02"))


def test_continuite_chevauchement(cfg, lot, base):
    reference = pd.concat([base, lot], ignore_index=True)   # le lot est déjà dans le snapshot
    assert "continuite_lot" in rules_failed(validate(lot, "lot", cfg, reference=reference, lot_name="lot-2012-01"))


def test_enforce_leve_une_exception_et_ecrit_le_rapport(cfg, lot, base, tmp_path):
    report = check(cfg, lot, base, lot.head(50))
    with pytest.raises(QualityError):
        enforce(report)
    path = report.write(tmp_path / "rapport.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["failures"] and data["kind"] == "lot"
