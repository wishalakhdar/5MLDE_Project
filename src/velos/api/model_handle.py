"""Le modèle servi par l'API et ce qu'il faut savoir de lui pour tracer chaque prédiction."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class ModelHandle:
    """Un modèle chargé + son identité dans le registre. Immuable : un rechargement crée un nouvel objet,
    les requêtes en cours gardent celui qu'elles ont reçu."""
    model: Any
    name: str
    alias: str
    version: int
    uri: str
    qualification: str
    data_origin: str
    loaded_at: datetime

    def info(self) -> dict:
        """Ce que l'API renvoie au client pour identifier la version utilisée."""
        return {"name": self.name, "version": self.version, "alias": self.alias,
                "qualification": self.qualification}


def load_champion(cfg: dict) -> ModelHandle:
    """Charge le modèle désigné par l'alias (models:/<nom>@champion) et lit ses tags de qualification.
    Lève RegistryError si l'alias est absent ou le registre injoignable."""
    from ..errors import RegistryError
    from ..predict import load_alias_model
    from ..registry import make_backend

    name = cfg["tracking"]["model_name"]
    alias = cfg["qualification"]["alias"]
    model, uri, version = load_alias_model(cfg, alias)        # fonction d'Aicha, réutilisée telle quelle
    try:
        tags = make_backend(cfg).get_tags(name, version)
    except Exception as exc:  # registre devenu injoignable entre les deux appels
        raise RegistryError(f"Tags de {name} v{version} illisibles : {type(exc).__name__}: {exc}") from exc
    return ModelHandle(
        model=model, name=name, alias=alias, version=int(version), uri=uri,
        qualification=tags.get("qualification", "inconnue"),
        data_origin=tags.get("data.origin", "inconnue"),
        loaded_at=datetime.now(timezone.utc),
    )
