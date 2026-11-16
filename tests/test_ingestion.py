import io
import json
import zipfile

import pandas as pd
import pytest

from velos.config import load_config, path_of
from velos.errors import ConfigError, DataIntegrityError
from velos.ingestion import extract_member, fetch, install_raw, verify_raw
from velos.state import sha256_bytes
import json as _json


def make_zip(member: str, content: bytes) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(member, content)
    return buf.getvalue()


@pytest.fixture
def empty_cfg(tmp_path):
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    (tmp_path / "config.json").write_text((root / "config.json").read_text(encoding="utf-8"), encoding="utf-8")
    return load_config(tmp_path / "config.json")


def test_extract_member(synthetic_csv_bytes):
    z = make_zip("Bike-Sharing-Dataset/hour.csv", synthetic_csv_bytes)
    assert extract_member(z, "hour.csv") == synthetic_csv_bytes
    with pytest.raises(ConfigError):
        extract_member(make_zip("autre.csv", b"x"), "hour.csv")


def test_fetch_zip_ecrit_manifeste_complet(empty_cfg, synthetic_csv_bytes, tmp_path):
    src = tmp_path / "archive.zip"
    src.write_bytes(make_zip("hour.csv", synthetic_csv_bytes))
    manifest = fetch(empty_cfg, from_file=src)
    assert manifest["sha256"] == sha256_bytes(synthetic_csv_bytes)
    for key in ("licence", "doi", "url", "citation"):
        assert manifest["source"][key]
    assert manifest["retrieved_at_utc"] and manifest["rows"] > 1000


def test_deux_executions_meme_empreinte(empty_cfg, synthetic_csv_bytes, tmp_path):
    src = tmp_path / "hour.csv"
    src.write_bytes(synthetic_csv_bytes)
    first = fetch(empty_cfg, from_file=src)
    second = fetch(empty_cfg, from_file=src)
    assert first["sha256"] == second["sha256"]


def test_fichier_different_refuse(empty_cfg, synthetic_csv_bytes):
    install_raw(empty_cfg, synthetic_csv_bytes)
    with pytest.raises(DataIntegrityError):
        install_raw(empty_cfg, synthetic_csv_bytes + b"0,0\n")


def test_fichier_brut_modifie_detecte(empty_cfg, synthetic_csv_bytes):
    install_raw(empty_cfg, synthetic_csv_bytes)
    raw = path_of(empty_cfg, "raw_dir") / "hour.csv"
    raw.write_bytes(synthetic_csv_bytes + b"1\n")
    with pytest.raises(DataIntegrityError):
        verify_raw(empty_cfg)


def test_colonnes_manquantes_refusees(empty_cfg, synthetic_csv_bytes):
    df = pd.read_csv(io.BytesIO(synthetic_csv_bytes)).drop(columns=["hum"])
    with pytest.raises(DataIntegrityError):
        install_raw(empty_cfg, df.to_csv(index=False).encode())
