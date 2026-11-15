import io
import json
import shutil
from pathlib import Path

import pytest

from velos.config import load_config
from velos.ingestion import install_raw
from velos.replay import init_store
from velos.synthetic import make_hour_frame

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def synthetic_csv_bytes():
    buf = io.StringIO()
    make_hour_frame(seed=1).to_csv(buf, index=False, lineterminator="\n")
    return buf.getvalue().encode()


@pytest.fixture
def cfg(tmp_path, synthetic_csv_bytes):
    """Projet isolé dans tmp_path, données SYNTHÉTIQUES (origin = synthetic), petit modèle."""
    conf = json.loads((ROOT / "config.json").read_text())
    conf["model"].update(n_estimators=8, max_depth=6)
    conf["tracking"]["enabled"] = False
    (tmp_path / "config.json").write_text(json.dumps(conf))
    shutil.copytree(ROOT / "policies", tmp_path / "policies")
    c = load_config(tmp_path / "config.json")
    install_raw(c, synthetic_csv_bytes, origin="synthetic")
    init_store(c)
    return c
