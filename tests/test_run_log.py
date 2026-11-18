"""Journal des traitements (tracing.pipeline_runs) : jamais bloquant, contenu correct."""
from datetime import datetime, timedelta, timezone

import pytest

from velos.run_log import make_entry, record_pipeline_run

T0 = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)


class FakeConn:
    def __init__(self, fail=None):
        self.calls, self.fail = [], fail

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params):
        if self.fail:
            raise self.fail
        self.calls.append((sql, params))


def test_entree_duree_et_erreur_tronquee():
    e = make_entry("velos-process-lot", "lot-2012-01", T0, T0 + timedelta(seconds=42), "rejected", error="x" * 900)
    assert e["duration_s"] == 42 and len(e["error"]) == 500 and e["model_version"] is None


def test_statut_inconnu_refuse():
    with pytest.raises(ValueError):
        make_entry("f", "base", T0, T0, "ok")


def test_enregistrement_avec_le_role_dedie():
    conn = FakeConn()
    seen = {}

    def connect(**kwargs):
        seen.update(kwargs)
        return conn
    e = make_entry("velos-process-lot", "base", T0, T0, "succeeded", model_version=3, verdict="production-ready")
    assert record_pipeline_run(e, {"PIPELINE_WRITER_PASSWORD": "pw", "POSTGRES_PORT": "5433"}, connect)
    assert seen["user"] == "pipeline_writer" and seen["port"] == "5433" and seen["host"] == "127.0.0.1"
    assert "tracing.pipeline_runs" in conn.calls[0][0] and conn.calls[0][1]["model_version"] == 3


def test_sans_mot_de_passe_rien_n_echoue():
    assert record_pipeline_run(make_entry("f", "base", T0, T0, "failed"), {}) is False


def test_base_indisponible_ne_leve_pas():
    def connect(**kwargs):
        return FakeConn(fail=ConnectionError("refusée"))
    assert record_pipeline_run(make_entry("f", "base", T0, T0, "failed"), {"PIPELINE_WRITER_PASSWORD": "pw"},
                               connect) is False
