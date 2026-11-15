"""Règles de mise en service (A5). `decide` est une fonction PURE : mêmes preuves + même politique = même verdict.

Verdicts :
  production-ready : tous les contrôles sont satisfaits (statut pédagogique, pas une validation métier) ;
  rejected         : candidat conservé pour analyse mais non publiable ;
  pending          : preuve manquante ou protocole incompatible (une preuve absente ne vaut JAMAIS acceptation) ;
  demo-only        : entraîné sur des données synthétiques.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

from .config import resolve

PRODUCTION_READY, REJECTED, PENDING, DEMO_ONLY = "production-ready", "rejected", "pending", "demo-only"


@dataclass
class Decision:
    verdict: str
    reasons: list = field(default_factory=list)


def load_policy(cfg: dict) -> dict:
    return json.loads(Path(resolve(cfg, cfg["qualification"]["policy"])).read_text(encoding="utf-8"))


def policy_ref(policy: dict) -> str:
    return f"{policy['id']}@{policy['version']}"


def _finite(x) -> bool:
    return isinstance(x, (int, float)) and math.isfinite(x)


def decide(evidence: dict, policy: dict, *, expected_corpus_sha: str | None, reload_check: dict | None) -> Decision:
    # 1. Provenance : un modèle entraîné sur données synthétiques n'est jamais qualifié comme un vrai modèle
    if evidence.get("data_origin") != "uci":
        return Decision(DEMO_ONLY, [f"données d'origine « {evidence.get('data_origin')} » (non UCI)"])

    # 2. Métriques présentes et finies
    split = policy["primary_metric"]["split"]
    metrics = evidence.get("metrics", {})
    val, base = metrics.get(split, {}), metrics.get(f"baseline_{split}", {})
    missing = [f"{split}.{m}" for m in policy["required_metrics"] if not _finite(val.get(m))]
    if not _finite(base.get(policy["primary_metric"]["name"])):
        missing.append(f"baseline_{split}.{policy['primary_metric']['name']}")
    if missing:
        return Decision(PENDING, [f"métriques absentes ou non finies : {missing}"])

    # 3. Même corpus d'examen que celui du magasin courant
    if expected_corpus_sha is None or evidence.get("corpus_sha256") != expected_corpus_sha:
        return Decision(PENDING, ["corpus de qualification différent ou inconnu : comparaison impossible"])

    # 4. Rechargement vérifié dans un nouveau processus
    reasons: list[str] = []
    if not reload_check or reload_check.get("status") != "ok":
        status = (reload_check or {}).get("status", "absent")
        if status == "absent":
            return Decision(PENDING, ["contrôle de rechargement non effectué"])
        return Decision(REJECTED, [f"rechargement du modèle en échec ({status})"])
    if reload_check.get("rows", 0) < policy["min_reload_rows"]:
        return Decision(PENDING, ["rechargement contrôlé sur trop peu de lignes"])
    if not _finite(reload_check.get("max_abs_delta")) or reload_check["max_abs_delta"] > policy["reload_tolerance"]:
        reasons.append(f"prédictions rechargées différentes (écart {reload_check.get('max_abs_delta')})")

    # 5. Seuil absolu, 6. gain minimal sur la baseline, 7. garde-fou par segment
    name = policy["primary_metric"]["name"]
    mae, base_mae = val[name], base[name]
    if mae > policy["max_mae"]:
        reasons.append(f"{name} = {mae:.2f} > seuil {policy['max_mae']}")
    gain = (base_mae - mae) / base_mae if base_mae else 0.0
    if gain < policy["min_gain_vs_baseline"]:
        reasons.append(f"gain sur la baseline = {gain:.1%} < {policy['min_gain_vs_baseline']:.0%}")
    guard = policy["segment_guard"]
    for key, seg in evidence.get("segments", {}).get(guard["column"], {}).items():
        if seg["rows"] >= guard["min_rows"] and seg["mae"] > guard["max_ratio_to_baseline"] * seg["baseline_mae"]:
            reasons.append(f"segment {guard['column']}={key} dégradé (mae {seg['mae']:.1f} > baseline {seg['baseline_mae']:.1f})")
    return Decision(REJECTED, reasons) if reasons else Decision(PRODUCTION_READY, ["tous les contrôles satisfaits"])
