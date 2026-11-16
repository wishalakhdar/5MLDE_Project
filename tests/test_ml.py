"""Pipeline ML : pas de fuite, imputation apprise sur le train, publication atomique et vérifiable."""
import json

import joblib
import numpy as np
import pandas as pd
import pytest
from sklearn.exceptions import NotFittedError
from sklearn.utils.validation import check_is_fitted

from velos.config import path_of
from velos.contract import FEATURES, LEAK_COLUMNS
from velos.evaluation import is_complete
from velos.pipeline import load_data, run_pipeline
from velos.predict import load_run_model, predict_frame
from velos.preprocessing import preprocess_data, split_chronological
from velos.tracking import NullTracker
from velos.training import build_pipeline


def test_features_sans_fuite():
    assert not set(FEATURES) & set(LEAK_COLUMNS)
    assert {"cnt", "casual", "registered"} <= set(LEAK_COLUMNS)


def test_split_chronologique_sans_chevauchement(cfg):
    prepared = preprocess_data(load_data(cfg, None, NullTracker()))
    assert not set(prepared.train_ids) & set(prepared.test_ids)
    snap = prepared.ingested.snapshot.set_index("instant")
    last_train = snap.loc[prepared.train_ids, "dteday"].max()
    first_test = snap.loc[prepared.test_ids, "dteday"].min()
    assert last_train <= first_test           # le test est plus récent que le train
    assert list(prepared.X_train.columns) == FEATURES


def test_preprocesseur_non_ajuste_avant_entrainement(cfg):
    prepared = preprocess_data(load_data(cfg, None, NullTracker()))
    with pytest.raises(NotFittedError):
        check_is_fitted(prepared.preprocessor)


def test_imputation_apprise_sur_le_train_seulement(cfg):
    df = pd.read_csv(path_of(cfg, "store_dir") / "base.csv")
    train, test = split_chronological(df, 0.15)
    test = test.copy(); test["temp"] = 0.99        # le test a une valeur très différente
    X_train = train[FEATURES].copy(); X_train.loc[X_train.index[:5], "temp"] = np.nan
    pipe = build_pipeline(cfg["model"], cfg["seed"])
    pipe.fit(X_train, train["cnt"])
    imputer = pipe.named_steps["prep"].named_transformers_["num"]
    expected = X_train.median().to_numpy()
    assert np.allclose(imputer.statistics_, expected)
    assert imputer.statistics_[FEATURES.index("temp")] != 0.99


def test_publication_et_integrite(cfg):
    res = run_pipeline(cfg, None, NullTracker())
    run_dir = res.run_dir
    pub = json.loads((run_dir / "publication.json").read_text(encoding="utf-8"))
    assert "publication.json" not in pub["files"]
    for name in ("model.joblib", "metrics.json", "evidence.json", "provenance.json", "quality-reports.json"):
        assert name in pub["files"]
    assert not list(run_dir.parent.glob("*.partial"))   # aucun dossier temporaire résiduel
    assert is_complete(run_dir)


def test_run_corrompu_ou_incomplet_detecte(cfg):
    run_dir = run_pipeline(cfg, None, NullTracker()).run_dir
    model = run_dir / "model.joblib"
    original = model.read_bytes()
    model.write_bytes(original[:-10])                  # fichier tronqué
    assert not is_complete(run_dir)
    model.write_bytes(original)
    assert is_complete(run_dir)
    (run_dir / "publication.json").unlink()
    assert not is_complete(run_dir)


def test_provenance_et_modele_rechargeable(cfg):
    res = run_pipeline(cfg, None, NullTracker())
    prov = json.loads((res.run_dir / "provenance.json").read_text(encoding="utf-8"))
    assert prov["origin"] == "synthetic" and len(prov["snapshot_sha256"]) == 64
    model = load_run_model(res.run_dir)
    sample = pd.read_csv(path_of(cfg, "store_dir") / "qualification.csv").head(20)
    first, second = predict_frame(model, sample), predict_frame(joblib.load(res.run_dir / "model.joblib"), sample)
    assert np.allclose(first, second) and (first >= 0).all()
