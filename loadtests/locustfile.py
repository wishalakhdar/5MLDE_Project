"""Scénario de charge Locust : des utilisateurs qui appellent l'API comme le ferait une application.

Répartition des appels (poids des tâches) :
  - 8 : une prédiction en ligne (1 heure à prévoir) — l'usage principal ;
  - 1 : une prédiction par lot (24 heures, une journée entière) ;
  - 1 : une prédiction suivie d'un feedback (valeur observée) ;
  - 1 : une entrée invalide (hr = 25) : 422 attendu, compté comme SUCCÈS car c'est le bon comportement.
Chaque utilisateur attend entre 0,5 et 1,5 s entre deux appels : N utilisateurs ≈ N requêtes/s proposées.

Lancé par run_config.py (protocole complet), ou à la main :
    uv run locust -f loadtests/locustfile.py --host http://127.0.0.1:8000
"""
from __future__ import annotations

import random

from locust import HttpUser, between, task

from velos.contract import FEATURES, INTEGER_FEATURES, RANGES


def random_instance(rng: random.Random) -> dict:
    """Une heure fictive mais VALIDE selon le contrat (valeurs tirées dans les plages autorisées)."""
    inst = {}
    for col in FEATURES:
        lo, hi = RANGES[col]
        inst[col] = rng.randint(lo, hi) if col in INTEGER_FEATURES else round(rng.uniform(lo, hi), 3)
    return inst


class ApiUser(HttpUser):
    wait_time = between(0.5, 1.5)

    def on_start(self):
        self.rng = random.Random()

    @task(8)
    def predict_one(self):
        self.client.post("/predict", json={"instances": [random_instance(self.rng)]}, name="/predict [1]")

    @task(1)
    def predict_day(self):
        self.client.post("/predict", json={"instances": [random_instance(self.rng) for _ in range(24)]},
                         name="/predict [24]")

    @task(1)
    def predict_then_feedback(self):
        r = self.client.post("/predict", json={"instances": [random_instance(self.rng)]}, name="/predict [1]")
        if r.status_code == 200:
            prediction_id = r.json()["predictions"][0]["prediction_id"]
            self.client.post("/feedback", json={"prediction_id": prediction_id,
                                                "observed_cnt": self.rng.randint(0, 900)}, name="/feedback")

    @task(1)
    def invalid_input(self):
        bad = {**random_instance(self.rng), "hr": 25}
        with self.client.post("/predict", json={"instances": [bad]}, name="/predict [invalide]",
                              catch_response=True) as r:
            if r.status_code == 422:
                r.success()          # refus attendu : le service se comporte correctement
            else:
                r.failure(f"422 attendu, reçu {r.status_code}")
