"""Synthèse des tests de charge : lecture des CSV Locust et règle de recommandation (sans lancer Locust)."""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("summarize", ROOT / "loadtests" / "summarize.py")
summarize = importlib.util.module_from_spec(spec)
spec.loader.exec_module(summarize)

HEADER = ("Type,Name,Request Count,Failure Count,Median Response Time,Average Response Time,Min Response Time,"
          "Max Response Time,Average Content Size,Requests/s,Failures/s,50%,66%,75%,80%,90%,95%,98%,99%,99.9%,"
          "99.99%,100%\n")
SLO = {"p95_ms": 500, "max_failure_ratio": 0.01, "target_users": 40}


def write_config(root, name, cpus, workers, levels):
    """levels : {utilisateurs: (requêtes, échecs, req/s, p95)}"""
    d = root / name
    d.mkdir()
    cfg = {"name": name, "API_CPUS": str(cpus), "API_WORKERS": str(workers), "API_MEMORY": "1g"}
    (d / "meta.json").write_text(json.dumps({"config": cfg, "levels": list(levels)}), encoding="utf-8")
    for users, (req, fail, rps, p95) in levels.items():
        (d / f"{users}u_stats.csv").write_text(
            HEADER + f",Aggregated,{req},{fail},20,30,5,900,100,{rps},0,20,25,30,35,40,{p95},{p95},{p95 * 2},"
                     f"{p95 * 3},{p95 * 3},{p95 * 3}\n", encoding="utf-8")
        (d / f"{users}u_resources.json").write_text(json.dumps({"cpu_pct_mean": 50.0, "cpu_pct_max": 90.0,
                                                                "mem_mib_max": 300.0}), encoding="utf-8")


def test_lecture_ligne_aggregated(tmp_path):
    write_config(tmp_path, "B", 1.0, 1, {10: (1000, 5, 22.2, 120)})
    row = summarize.collect(tmp_path)[0]
    assert (row["config"], row["users"], row["requests"], row["p95_ms"]) == ("B", 10, 1000, 120.0)
    assert row["failure_ratio"] == 0.005 and row["cpu_pct_max"] == 90.0


def test_recommandation_la_moins_couteuse_qui_tient_l_objectif(tmp_path):
    write_config(tmp_path, "A-petite", 0.5, 1, {40: (1000, 0, 30.0, 1500)})     # trop lente
    write_config(tmp_path, "B-moyenne", 1.0, 1, {40: (1000, 3, 38.0, 300)})     # tient l'objectif
    write_config(tmp_path, "D-grande", 2.0, 2, {40: (1000, 0, 40.0, 90)})       # tient aussi, mais plus chère
    best = summarize.recommend(summarize.collect(tmp_path), SLO)
    assert best["config"] == "B-moyenne"


def test_trop_d_echecs_refuse_meme_si_rapide(tmp_path):
    write_config(tmp_path, "B", 1.0, 1, {40: (1000, 50, 38.0, 100)})            # 5 % d'échecs
    assert summarize.recommend(summarize.collect(tmp_path), SLO) is None


def test_capacite_et_rapport(tmp_path):
    write_config(tmp_path, "B", 1.0, 1, {20: (1000, 0, 19.0, 100), 40: (1000, 0, 37.0, 300),
                                         80: (1000, 200, 45.0, 4000)})
    rows = summarize.collect(tmp_path)
    assert summarize.capacity(rows, SLO) == {"B": 40}
    md = summarize.to_markdown(rows, SLO)
    assert "**Recommandation** : B" in md and "❌" in md


def test_main_ecrit_les_fichiers(tmp_path):
    write_config(tmp_path, "B", 1.0, 1, {40: (1000, 0, 37.0, 300)})
    protocol = tmp_path / "configs.json"
    protocol.write_text(json.dumps({"slo": SLO}), encoding="utf-8")
    assert summarize.main(["--protocol", str(protocol), "--results", str(tmp_path)]) == 0
    assert (tmp_path / "summary.csv").exists() and (tmp_path / "summary.md").exists()
