"""Exceptions du projet. Distinguer erreur transitoire et erreur permanente guide les reprises (retries)."""


class VelosError(Exception):
    """Base de toutes les erreurs du projet."""


class ConfigError(VelosError, ValueError):
    """Configuration absente ou invalide."""


class DataIntegrityError(VelosError):
    """Un fichier de données ne correspond pas à son empreinte SHA-256 attendue."""


class QualityError(VelosError):
    """Un contrôle qualité bloquant a échoué : le pipeline doit s'arrêter."""

    def __init__(self, report):
        self.report = report
        super().__init__(report.summary())


class InvalidInput(VelosError, ValueError):
    """Entrée de prédiction invalide (réutilisable par l'API : renvoyer 422)."""

    def __init__(self, problems):
        self.problems = list(problems)
        super().__init__("; ".join(self.problems))


class AlreadyProcessed(VelosError):
    """Le lot a déjà été accepté (traitement idempotent : rien à refaire)."""


class TransientError(VelosError):
    """Panne passagère (réseau, service indisponible) : une nouvelle tentative a un sens."""


class RegistryError(VelosError):
    """Problème avec le registre de modèles."""


class SelectionRefused(RegistryError):
    """Une version ne peut pas recevoir l'alias de mise en service."""
