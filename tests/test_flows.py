"""Tests des parties pures de l'orchestration. Ignorés si Prefect n'est pas installé."""
import pytest

pytest.importorskip("prefect")

from velos.errors import TransientError  # noqa: E402
from velos.flows import _retries, retry_only_transient  # noqa: E402


class FakeState:
    def __init__(self, exc=None):
        self.exc = exc

    def result(self):
        if self.exc:
            raise self.exc
        return 1


def test_retry_seulement_si_erreur_transitoire():
    assert retry_only_transient(None, None, FakeState(TransientError("réseau")))
    assert retry_only_transient(None, None, FakeState(ConnectionError()))
    assert not retry_only_transient(None, None, FakeState(ValueError("données invalides")))
    assert not retry_only_transient(None, None, FakeState())


def test_reprises_bornees_par_la_configuration(cfg):
    opts = _retries(cfg)
    assert opts["retries"] == cfg["retry"]["max_retries"] == 2
    assert opts["retry_condition_fn"] is retry_only_transient
