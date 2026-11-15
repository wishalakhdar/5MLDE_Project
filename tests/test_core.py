import json

import pandas as pd
import pytest

from velos import replay
from velos.alerts import is_transient, send_alert
from velos.config import load_config, path_of
from velos.contract import FEATURES, validate_inference_frame
from velos.errors import (AlreadyProcessed, DataIntegrityError, InvalidInput, QualityError, TransientError,
                          ConfigError)
from velos.evaluation import is_complete
from velos.ingestion import download, install_raw
from velos.pipeline import commit_lot, run_pipeline
from velos.predict import load_run_model, predict_frame
from velos.state import accepted_ids
from velos.tracking import NullTracker


def test_config_missing(tmp_path):
    with pytest.raises(ConfigError):
        load_config(tmp_path / "nope.json")


def test_manifest_tamper_refused(cfg, synthetic_csv_bytes):
    with pytest.raises(DataIntegrityError):
        install_raw(cfg, synthetic_csv_bytes + b"\n1,2")


def test_download_retries_only_transient():
    calls = []

    class R:
        def __init__(self, code, content=b"x"):
            self.status_code, self.content = code, content
        def raise_for_status(self):
            if self.status_code >= 400:
                import requests
                raise requests.HTTPError(response=self)

    seq = [R(503), R(200)]
    def opener(url, timeout=0):
        calls.append(1)
        return seq.pop(0)
    assert download("http://x", opener=opener, sleep=lambda s: None) == b"x"
    assert len(calls) == 2
    with pytest.raises(Exception):
        download("http://x", opener=lambda u, timeout=0: R(404), sleep=lambda s: None)


def test_replay_deterministic_and_next_lot(cfg):
    m1 = replay.init_store(cfg)
    assert m1 == replay.init_store(cfg)
    lots = replay.pending_lots(cfg)
    assert lots[0] == "lot-2012-01" and len(lots) == 11
    assert replay.next_lot(cfg).name == "lot-2012-01.csv"


def test_base_run_is_demo_only_and_complete(cfg):
    res = run_pipeline(cfg, None, NullTracker())
    assert is_complete(res.run_dir)
    ev = json.loads((res.run_dir / "evidence.json").read_text())
    assert ev["data_origin"] == "synthetic"
    assert res.metrics["test"]["mae"] < res.metrics["baseline_test"]["mae"]
    # pas de fuite : aucune colonne cible/leak dans les features
    assert not {"cnt", "casual", "registered"} & set(FEATURES)


def test_good_lot_accepted_and_idempotent(cfg):
    lot = replay.next_lot(cfg)
    res = run_pipeline(cfg, lot, NullTracker())
    commit_lot(cfg, res)
    assert accepted_ids(cfg) == ["lot-2012-01"]
    assert not lot.exists()
    again = replay.path_of(cfg, "incoming_dir") / "lot-2012-01.csv"
    import shutil
    shutil.copyfile(path_of(cfg, "state_dir") / "accepted" / "lot-2012-01.csv", again)
    with pytest.raises(AlreadyProcessed):
        run_pipeline(cfg, again, NullTracker())
    assert len(list(path_of(cfg, "runs_dir").glob("run-*"))) == 1


@pytest.mark.parametrize("kind", ["negatif", "colonne", "volume"])
def test_bad_lot_blocks_everything(cfg, kind):
    lot = replay.make_bad_lot(cfg, kind)
    tr = NullTracker()
    with pytest.raises(QualityError):
        run_pipeline(cfg, lot, tr)
    assert tr.status == "FAILED"
    assert not lot.exists()                                   # mis en quarantaine
    assert list(path_of(cfg, "rejected_dir").glob("*.rapport.json"))
    assert not path_of(cfg, "runs_dir").exists() or not list(path_of(cfg, "runs_dir").glob("run-*"))
    assert accepted_ids(cfg) == []


def test_predict_and_contract(cfg):
    res = run_pipeline(cfg, None, NullTracker())
    model = load_run_model(res.run_dir)
    df = pd.read_csv(path_of(cfg, "store_dir") / "qualification.csv")[FEATURES].head(5)
    assert (predict_frame(model, df) >= 0).all()
    bad = df.copy(); bad.loc[0, "hr"] = 99
    with pytest.raises(InvalidInput):
        validate_inference_frame(bad)
    with pytest.raises(InvalidInput):
        validate_inference_frame(df.drop(columns=["hum"]))


def test_incomplete_run_refused(cfg, tmp_path):
    (tmp_path / "r").mkdir()
    assert not is_complete(tmp_path / "r")
    with pytest.raises(ConfigError):
        load_run_model(tmp_path / "r")


def test_is_transient_and_alert():
    assert is_transient(TransientError("x")) and is_transient(ConnectionError())
    assert not is_transient(ValueError()) and not is_transient(DataIntegrityError("x"))
    sent = {}
    class Resp:
        def raise_for_status(self): pass
    assert send_alert("hi", "http://w", post=lambda u, json, timeout: sent.update(j=json) or Resp())
    assert sent["j"] == {"text": "hi"}
    def boom(*a, **k): raise RuntimeError
    assert send_alert("hi", "http://w", post=boom) is False
    assert send_alert("hi", "") is False


def test_missing_lot_clean_error(cfg, tmp_path):
    with pytest.raises(ConfigError):
        run_pipeline(cfg, tmp_path / "absent.csv", NullTracker())
