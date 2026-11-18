"""Petites corrections : messages d'erreur lisibles, auteur des sélections tracé sans valeur en dur."""
import numpy as np
import pandas as pd
import pytest

from velos.cli import build_parser, default_actor
from velos.contract import _show, validate_inference_frame
from velos.errors import InvalidInput


def test_valeur_lisible_dans_les_messages():
    assert _show(np.int64(25)) == "25" and _show("gratuit") == "'gratuit'" and _show(0.5) == "0.5"


def test_message_hors_plage_sans_type_numpy():
    row = {"season": 3, "yr": 1, "mnth": 7, "hr": 25, "holiday": 0, "weekday": 3, "workingday": 1,
           "weathersit": 1, "temp": 0.7, "atemp": 0.66, "hum": 0.45, "windspeed": 0.15}
    with pytest.raises(InvalidInput) as exc:
        validate_inference_frame(pd.DataFrame([row]))
    assert "(valeur : 25)" in exc.value.problems[0] and "np." not in exc.value.problems[0]


def test_auteur_par_defaut_jamais_en_dur(monkeypatch):
    monkeypatch.setenv("VELOS_ACTOR", "jeffrey")
    assert default_actor() == "jeffrey"
    args = build_parser().parse_args(["select", "--version", "1", "--reason", "x"])
    assert args.actor is None          # résolu ensuite par main() : variable VELOS_ACTOR ou utilisateur du système
