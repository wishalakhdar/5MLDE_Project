"""Contrat d'entrée du modèle. Réutilisé tel quel par l'API de prédiction (phase 2)."""
from __future__ import annotations

import pandas as pd

from .errors import InvalidInput

FEATURES = [
    "season", "yr", "mnth", "hr", "holiday", "weekday",
    "workingday", "weathersit", "temp", "atemp", "hum", "windspeed",
]
INTEGER_FEATURES = ["season", "yr", "mnth", "hr", "holiday", "weekday", "workingday", "weathersit"]
TARGET = "cnt"
# casual + registered = cnt : les utiliser comme features serait une fuite de la cible.
LEAK_COLUMNS = ["casual", "registered", "cnt"]
RAW_COLUMNS = ["instant", "dteday", *FEATURES, "casual", "registered", "cnt"]

RANGES = {
    "season": (1, 4), "yr": (0, 1), "mnth": (1, 12), "hr": (0, 23),
    "holiday": (0, 1), "weekday": (0, 6), "workingday": (0, 1), "weathersit": (1, 4),
    "temp": (0.0, 1.0), "atemp": (0.0, 1.0), "hum": (0.0, 1.0), "windspeed": (0.0, 1.0),
}

MAX_REPORTED = 5


def _show(value) -> str:
    """Valeur lisible dans un message d'erreur : 25 (et non np.int64(25)), 'gratuit' entre guillemets."""
    return repr(value.item() if hasattr(value, "item") else value)


def validate_inference_frame(df) -> pd.DataFrame:
    """Vérifie une entrée de prédiction. Lève InvalidInput (à traduire en HTTP 422) avec la liste
    des problèmes. Retourne un DataFrame typé comme à l'entraînement. Les colonnes supplémentaires
    (dont casual, registered, cnt) sont ignorées."""
    if not isinstance(df, pd.DataFrame) or df.empty:
        raise InvalidInput(["entrée vide ou qui n'est pas un tableau de lignes"])
    missing = [c for c in FEATURES if c not in df.columns]
    if missing:
        raise InvalidInput([f"colonnes manquantes : {missing}"])

    raw = df[FEATURES]
    num = raw.apply(pd.to_numeric, errors="coerce")
    problems: list[str] = []
    for col in FEATURES:
        lo, hi = RANGES[col]
        is_null = raw[col].isna()
        not_numeric = num[col].isna() & ~is_null
        out = num[col].notna() & ~num[col].between(lo, hi)
        fractional = num[col].notna() & (num[col] % 1 != 0) if col in INTEGER_FEATURES else pd.Series(False, index=df.index)
        for mask, text in [
            (is_null, "valeur manquante"),
            (not_numeric, "valeur non numérique"),
            (out, f"hors de [{lo}, {hi}]"),
            (fractional, "doit être un entier"),
        ]:
            for idx in list(df.index[mask][:MAX_REPORTED]):
                problems.append(f"ligne {idx} : {col} {text} (valeur : {_show(raw.at[idx, col])})")
    if problems:
        raise InvalidInput(problems)
    return num.astype({c: "int64" for c in INTEGER_FEATURES})
