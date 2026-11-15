"""Étape 2 : fabriquer les features et séparer train / test SANS FUITE.

Principes :
  - features = uniquement des informations connues avant la location (météo, calendrier) ;
  - casual / registered / cnt ne sont JAMAIS des features (cnt = casual + registered) ;
  - le split est CHRONOLOGIQUE (le test est plus récent que le train, comme en production) ;
  - l'imputation est apprise sur le train seulement (voir training.py : fit sur X_train).
"""
from __future__ import annotations

import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer

from .contract import FEATURES, LEAK_COLUMNS, TARGET
from .stages import IngestedData, PreparedData


def build_preprocessor() -> ColumnTransformer:
    return ColumnTransformer(
        [("num", SimpleImputer(strategy="median"), FEATURES)],
        verbose_feature_names_out=False,
    )


def split_chronological(df: pd.DataFrame, test_fraction: float):
    ordered = df.sort_values(["dteday", "hr"]).reset_index(drop=True)
    n_test = max(1, int(round(len(ordered) * test_fraction)))
    return ordered.iloc[:-n_test], ordered.iloc[-n_test:]


def preprocess_data(data: IngestedData) -> PreparedData:
    cfg = data.cfg
    train, test = split_chronological(data.snapshot, cfg["split"]["test_fraction"])
    corpus = data.corpus.sort_values(["dteday", "hr"]).reset_index(drop=True)
    assert not set(FEATURES) & set(LEAK_COLUMNS), "une colonne interdite est utilisée comme feature"
    prepared = PreparedData(
        ingested=data,
        X_train=train[FEATURES], y_train=train[TARGET],
        X_test=test[FEATURES], y_test=test[TARGET],
        train_ids=train["instant"].tolist(), test_ids=test["instant"].tolist(),
        X_val=corpus[FEATURES], y_val=corpus[TARGET], val_frame=corpus,
        preprocessor=build_preprocessor(),
    )
    data.tracker.log_params({
        "data.rows.snapshot": len(data.snapshot), "data.rows.train": len(train),
        "data.rows.test": len(test), "data.rows.corpus": len(corpus),
        "split.test_fraction": cfg["split"]["test_fraction"], "split.kind": "chronological",
        "features": ",".join(FEATURES),
    })
    return prepared
