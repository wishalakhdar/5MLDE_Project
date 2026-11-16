"""Chargement par alias, liste des versions et garde-fous de l'historique."""
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from velos.errors import ConfigError, RegistryError, SelectionRefused
from velos.lifecycle import describe_versions, rollback, select_version
from velos.predict import load_alias_model, predict_file_alias
from velos.registry import InMemoryRegistry
from velos.cli import main

from test_lifecycle import N, SHA, ev, setup, sel  # noqa: E402  (réutilise les fabriques)


class FakeModel:
    def predict(self, df):
        return np.full(len(df), -5.0)   # valeur négative : doit être ramenée à 0


def test_chargement_par_alias(cfg):
    model, uri, version = load_alias_model(cfg, "champion", loader=lambda u: (FakeModel(), 7))
    assert uri == "models:/velos-demand-cnt@champion" and version == 7


def test_alias_introuvable_message_clair(cfg):
    def boom(uri):
        raise RuntimeError("alias absent")
    with pytest.raises(RegistryError) as exc:
        load_alias_model(cfg, "champion", loader=boom)
    assert "models:/velos-demand-cnt@champion" in str(exc.value)


def test_prediction_par_alias_valide_et_ecrit(cfg, tmp_path):
    src = Path(cfg["_base_dir"]) / "data" / "store" / "qualification.csv"
    out, uri, version = predict_file_alias(cfg, src, tmp_path / "p.csv", loader=lambda u: (FakeModel(), 3))
    df = pd.read_csv(out)
    assert (df["cnt_pred"] == 0).all() and (df["model_version"] == 3).all() and len(df) > 100


def test_cli_exige_une_seule_source(cfg, capsys):
    code = main(["--config", cfg["_config_path"], "predict", "--input", "x.csv", "--output", "y.csv"])
    assert code == 3 and "exactement une option" in capsys.readouterr().out


def test_describe_versions(tmp_path):
    policy = json.loads((Path(__file__).parents[1] / "policies/qualification-v1.json").read_text())
    reg = setup(policy, 60.0, 50.0)
    sel(reg, 1, policy, tmp_path / "h.jsonl")
    rows = describe_versions(reg, N, "champion", policy)
    assert [r["version"] for r in rows] == [1, 2]
    assert rows[0]["alias"] == "champion" and rows[1]["alias"] == ""
    assert rows[1]["value"] == 50.0 and rows[1]["verdict"] == "production-ready"


def test_historique_incoherent_avec_le_registre(tmp_path):
    policy = json.loads((Path(__file__).parents[1] / "policies/qualification-v1.json").read_text())
    h = tmp_path / "h.jsonl"
    reg = setup(policy, 60.0, 40.0)
    sel(reg, 1, policy, h)
    sel(reg, 2, policy, h)
    # autre serveur MLflow : le registre n'a pas le même alias que l'historique
    other = setup(policy, 60.0, 40.0)
    with pytest.raises(RegistryError):
        rollback(other, N, alias="champion", actor="t", history_path=h)
    with pytest.raises(SelectionRefused):
        sel(other, 1, policy, h)
