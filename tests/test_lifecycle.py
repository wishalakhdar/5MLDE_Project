import json

import pytest

from velos.errors import RegistryError, SelectionRefused
from velos.lifecycle import qualify_version, rollback, select_version
from velos.qualification import decide, load_policy
from velos.registry import InMemoryRegistry
from velos.release import compare_predictions, release_model
from velos.pipeline import run_pipeline
from velos.tracking import NullTracker
from velos.state import read_store_manifest

SHA = "abc"
OK_RELOAD = {"status": "ok", "rows": 200, "max_abs_delta": 0.0}
N = "velos"


def ev(mae=60.0, base=150.0, origin="uci", sha=SHA, seg=None):
    return {"data_origin": origin, "corpus_sha256": sha,
            "metrics": {"validation": {"mae": mae, "rmse": 90.0, "r2": 0.8},
                        "baseline_validation": {"mae": base}},
            "segments": {"weathersit": seg or {}}}


@pytest.fixture
def policy():
    return json.loads((__import__("pathlib").Path(__file__).parents[1] / "policies/qualification-v1.json").read_text())


def test_decide_cases(policy):
    d = lambda e, r=OK_RELOAD, sha=SHA: decide(e, policy, expected_corpus_sha=sha, reload_check=r).verdict
    assert d(ev()) == "production-ready"
    assert d(ev(origin="synthetic")) == "demo-only"
    assert d(ev(mae=200, base=400)) == "rejected"            # seuil absolu
    assert d(ev(mae=140, base=150)) == "rejected"            # gain insuffisant
    assert d(ev(), sha="autre") == "pending"
    assert d(ev(), r=None) == "pending"                      # preuve absente != acceptation
    assert d(ev(), r={"status": "echec-chargement", "rows": 0}) == "rejected"
    bad_seg = {"3": {"rows": 50, "mae": 90.0, "baseline_mae": 80.0}}
    assert d(ev(seg=bad_seg)) == "rejected"
    e = ev(); e["metrics"]["validation"]["mae"] = float("nan")
    assert d(e) == "pending"


def setup(policy, *maes):
    reg = InMemoryRegistry()
    for i, m in enumerate(maes):
        v = reg.register("u", N, f"r{i}", {"pipeline.run": f"p{i}", "qualification": "pending"}, evidence=ev(mae=m))
        reg.set_tags(N, v, {"reload.check": json.dumps(OK_RELOAD)})
        qualify_version(reg, N, v, policy, SHA)
    return reg


def sel(reg, v, policy, hist, **kw):
    return select_version(reg, N, v, policy=policy, expected_corpus_sha=SHA, alias="champion", actor="t",
                          reason="r", history_path=hist, **kw)


def test_manual_tag_not_enough(policy, tmp_path):
    reg = setup(policy, 140.0)                                 # rejeté (gain trop faible)
    assert reg.get_tags(N, 1)["qualification"] == "rejected"
    reg.set_tags(N, 1, {"qualification": "production-ready", "qualification.policy": "velos-qualification@1.0.0"})
    with pytest.raises(SelectionRefused):
        sel(reg, 1, policy, tmp_path / "h.jsonl")
    assert reg.get_alias(N, "champion") is None


def test_select_replacement_gain_and_rollback(policy, tmp_path):
    h = tmp_path / "h.jsonl"
    reg = setup(policy, 60.0, 59.8, 40.0)
    sel(reg, 1, policy, h)
    with pytest.raises(SelectionRefused):
        sel(reg, 2, policy, h)                                 # gain 0,3 % < 1 %
    assert reg.get_alias(N, "champion") == 1
    sel(reg, 3, policy, h, dry_run=True)
    assert reg.get_alias(N, "champion") == 1
    sel(reg, 3, policy, h)
    assert reg.get_alias(N, "champion") == 3
    rollback(reg, N, alias="champion", actor="t", history_path=h)
    assert reg.get_alias(N, "champion") == 1
    with pytest.raises(RegistryError):
        rollback(reg, N, alias="champion", actor="t", history_path=h)


def test_rollback_without_previous(policy, tmp_path):
    reg = setup(policy, 60.0)
    sel(reg, 1, policy, tmp_path / "h.jsonl")
    with pytest.raises(RegistryError):
        rollback(reg, N, alias="champion", actor="t", history_path=tmp_path / "h.jsonl")


def test_policy_change_requires_requalification(policy, tmp_path):
    reg = setup(policy, 60.0)
    new = dict(policy, version="2.0.0")
    with pytest.raises(SelectionRefused):
        sel(reg, 1, new, tmp_path / "h.jsonl")


def test_compare_predictions():
    import numpy as np
    assert compare_predictions(np.ones(3), np.ones(3), 1e-6)["status"] == "ok"
    assert compare_predictions(np.ones(3), np.ones(3) + 1, 1e-6)["status"] == "echec-ecart"
    assert compare_predictions(np.ones(3), np.ones(2), 1e-6)["status"] == "echec-forme"


def test_release_end_to_end_synthetic_is_demo_only(cfg):
    res = run_pipeline(cfg, None, NullTracker())
    reg = InMemoryRegistry()
    out = release_model(cfg, res.run_dir, reg, reload_verifier=lambda *a: OK_RELOAD)
    assert out["verdict"] == "demo-only" and out["version"] == 1
    again = release_model(cfg, res.run_dir, reg, reload_verifier=lambda *a: OK_RELOAD)
    assert again["version"] == 1 and len(reg.versions) == 1     # pas de doublon
