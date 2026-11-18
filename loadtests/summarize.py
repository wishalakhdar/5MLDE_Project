"""Synthèse des tests de charge et recommandation de dimensionnement fondée sur les mesures.

Lit loadtests/results/<configuration>/ (produit par run_config.py) et écrit :
  - results/summary.csv : une ligne par configuration et par palier ;
  - results/summary.md  : tableau lisible + recommandation.
Règle de recommandation (fixée dans configs.json AVANT les mesures) : la configuration la moins coûteuse
(CPU, puis nombre de processus) qui respecte l'objectif au palier cible : p95 ≤ slo.p95_ms et taux
d'échec ≤ slo.max_failure_ratio.

Usage : uv run python loadtests/summarize.py
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _num(value: str) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None   # Locust écrit « N/A » quand il n'y a pas eu de requête


def read_aggregated(stats_csv: Path) -> dict:
    """Ligne « Aggregated » d'un fichier <prefix>_stats.csv de Locust (toutes les routes confondues)."""
    with open(stats_csv, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if row["Name"] == "Aggregated":
                requests = int(row["Request Count"])
                failures = int(row["Failure Count"])
                return {"requests": requests, "failures": failures,
                        "failure_ratio": failures / requests if requests else None,
                        "rps": _num(row["Requests/s"]), "p50_ms": _num(row["50%"]),
                        "p95_ms": _num(row["95%"]), "p99_ms": _num(row["99%"])}
    raise ValueError(f"Ligne Aggregated absente de {stats_csv}")


def collect(results_dir: Path) -> list[dict]:
    rows = []
    for meta_path in sorted(results_dir.glob("*/meta.json")):
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        cfg = meta["config"]
        for users in meta["levels"]:
            stats = meta_path.parent / f"{users}u_stats.csv"
            if not stats.exists():
                continue
            res_path = meta_path.parent / f"{users}u_resources.json"
            resources = json.loads(res_path.read_text(encoding="utf-8")) if res_path.exists() else {}
            rows.append({"config": cfg["name"], "cpus": float(cfg["API_CPUS"]), "workers": int(cfg["API_WORKERS"]),
                         "memory": cfg["API_MEMORY"], "users": users, **read_aggregated(stats),
                         "cpu_pct_mean": resources.get("cpu_pct_mean"), "cpu_pct_max": resources.get("cpu_pct_max"),
                         "mem_mib_max": resources.get("mem_mib_max")})
    return rows


def meets_slo(row: dict, slo: dict) -> bool:
    return (row["p95_ms"] is not None and row["failure_ratio"] is not None
            and row["p95_ms"] <= slo["p95_ms"] and row["failure_ratio"] <= slo["max_failure_ratio"])


def recommend(rows: list[dict], slo: dict) -> dict | None:
    """Configuration la moins coûteuse respectant l'objectif au palier cible (None si aucune)."""
    at_target = [r for r in rows if r["users"] == slo["target_users"] and meets_slo(r, slo)]
    return min(at_target, key=lambda r: (r["cpus"], r["workers"]), default=None)


def capacity(rows: list[dict], slo: dict) -> dict[str, int]:
    """Plus grand palier mesuré qui respecte encore l'objectif, par configuration."""
    best: dict[str, int] = {}
    for r in rows:
        if meets_slo(r, slo):
            best[r["config"]] = max(best.get(r["config"], 0), r["users"])
    return best


def fmt(value, digits=0) -> str:
    return "—" if value is None else f"{value:.{digits}f}"


def to_markdown(rows: list[dict], slo: dict) -> str:
    lines = [f"Objectif : p95 ≤ {slo['p95_ms']} ms et échecs ≤ {slo['max_failure_ratio']:.0%} "
             f"au palier cible de {slo['target_users']} utilisateurs.", "",
             "| Configuration | Utilisateurs | Req/s | p50 (ms) | p95 (ms) | p99 (ms) | Échecs | CPU moy. (%) "
             "| CPU max (%) | Mémoire max (Mio) | Objectif |",
             "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|:---:|"]
    for r in rows:
        ratio = "—" if r["failure_ratio"] is None else f"{r['failure_ratio']:.1%}"
        lines.append(f"| {r['config']} | {r['users']} | {fmt(r['rps'], 1)} | {fmt(r['p50_ms'])} | {fmt(r['p95_ms'])} "
                     f"| {fmt(r['p99_ms'])} | {ratio} | {fmt(r['cpu_pct_mean'])} | {fmt(r['cpu_pct_max'])} "
                     f"| {fmt(r['mem_mib_max'])} | {'✅' if meets_slo(r, slo) else '❌'} |")
    lines += ["", "Capacité mesurée (plus grand palier respectant l'objectif) : "
              + (", ".join(f"{k} → {v} utilisateurs" for k, v in capacity(rows, slo).items()) or "aucune")]
    best = recommend(rows, slo)
    lines += ["", "**Recommandation** : " + (
        f"{best['config']} ({best['cpus']:g} CPU, {best['workers']} processus, {best['memory']} de mémoire) : "
        f"p95 = {fmt(best['p95_ms'])} ms et {best['rps']:.1f} req/s à {best['users']} utilisateurs."
        if best else "aucune configuration mesurée ne respecte l'objectif au palier cible.")]
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Synthèse des tests de charge")
    ap.add_argument("--protocol", default=str(HERE / "configs.json"))
    ap.add_argument("--results", default=str(HERE / "results"))
    args = ap.parse_args(argv)
    slo = json.loads(Path(args.protocol).read_text(encoding="utf-8"))["slo"]
    results = Path(args.results)
    rows = collect(results)
    if not rows:
        print(f"Aucun résultat dans {results} : lancez d'abord run_config.py")
        return 1
    with open(results / "summary.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    markdown = to_markdown(rows, slo)
    (results / "summary.md").write_text(markdown, encoding="utf-8")
    print(markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
