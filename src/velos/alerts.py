"""Alertes en cas d'échec + règle « quelles erreurs mérite-t-on de rejouer ? »."""
from __future__ import annotations

import logging
import os

import requests

from .errors import QualityError, TransientError

log = logging.getLogger("velos.alerts")


def is_transient(exc: BaseException) -> bool:
    """True = panne passagère, une nouvelle tentative a un sens (réseau coupé, 429, 5xx).
    False = erreur permanente : rejouer ne changerait rien (données invalides, schéma, fichier absent...)."""
    if isinstance(exc, QualityError):
        return False
    if isinstance(exc, (TransientError, ConnectionError, TimeoutError, requests.ConnectionError, requests.Timeout)):
        return True
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        return exc.response.status_code in (429, 500, 502, 503, 504)
    return False


def send_alert(text: str, webhook_url: str | None = None, *, post=requests.post, timeout: int = 10) -> bool:
    """Envoie l'alerte au webhook (ex. Teams Workflows). Sans URL : simple log, retourne False.
    Ne lève jamais : une alerte qui échoue ne doit pas masquer l'incident d'origine."""
    url = webhook_url if webhook_url is not None else os.environ.get("ALERT_WEBHOOK_URL", "")
    if not url:
        log.error("ALERTE (aucun webhook configuré) : %s", text)
        return False
    try:
        resp = post(url, json={"text": text}, timeout=timeout)
        resp.raise_for_status()
        return True
    except Exception as exc:
        log.error("Échec d'envoi de l'alerte (%s) : %s", exc, text)
        return False
