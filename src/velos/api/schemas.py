"""Forme des corps JSON. Pydantic vérifie la STRUCTURE ; les valeurs des features sont vérifiées
ensuite par le contrat du modèle (velos.contract.validate_inference_frame), pour n'avoir qu'une seule
définition des règles métier."""
from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, Field

# Une valeur de feature est un nombre (ou un texte, qui sera refusé s'il n'est pas numérique) ;
# les objets et listes imbriqués sont refusés dès la lecture du JSON.
FeatureValue = float | int | str | None


class PredictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    instances: list[dict[str, FeatureValue]] = Field(min_length=1)


class FeedbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prediction_id: uuid.UUID
    observed_cnt: float = Field(ge=0, allow_inf_nan=False)
