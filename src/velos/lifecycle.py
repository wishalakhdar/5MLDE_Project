"""Cycle de vie d'une version : qualifier, sélectionner (alias « champion »), revenir en arrière.

Règles :
  - un TAG saisi à la main ne suffit jamais : `select_version` RECALCULE la décision depuis les preuves ;
  - remplacer un champion exige un gain minimal (policy.min_replacement_gain) ;
  - le retour arrière rejoue l'historique (registry/selection-history.jsonl) et ne demande pas de gain.
"""
from __future__ import annotations

import json
from pathlib import Path

from .errors import RegistryError, SelectionRefused
from .qualification import DEMO_ONLY, PRODUCTION_READY, REJECTED, Decision, decide, policy_ref
from .registry import RegistryBackend
from .state import now_utc


def _reload_check(tags: dict) -> dict | None:
    raw = tags.get("reload.check")
    return json.loads(raw) if raw else None


def evaluate_version(backend: RegistryBackend, name: str, version: int, policy: dict,
                     expected_corpus_sha: str | None) -> Decision:
    """Décision recalculée depuis les PREUVES (artefacts), pas depuis les tags de qualification."""
    evidence = backend.get_evidence(name, version)
    return decide(evidence, policy, expected_corpus_sha=expected_corpus_sha,
                  reload_check=_reload_check(backend.get_tags(name, version)))


def qualify_version(backend: RegistryBackend, name: str, version: int, policy: dict,
                    expected_corpus_sha: str | None, report_dir=None) -> Decision:
    decision = evaluate_version(backend, name, version, policy, expected_corpus_sha)
    tags = {
        "qualification": decision.verdict,
        "qualification.policy": policy_ref(policy),
        "qualification.date": now_utc(),
        "qualification.reason": " | ".join(decision.reasons)[:1000],
    }
    if report_dir is not None:
        report = Path(report_dir) / f"qualification-{name}-v{version}.json"
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps({"model": name, "version": version, "policy": policy_ref(policy),
                                      "verdict": decision.verdict, "reasons": decision.reasons,
                                      "decided_at_utc": tags["qualification.date"]},
                                     indent=2, ensure_ascii=False), encoding="utf-8")
        tags["qualification.report"] = str(report)
    backend.set_tags(name, version, tags)
    return decision


def _read_history(path) -> list[dict]:
    p = Path(path)
    if not p.is_file():
        return []
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]


def _append_history(path, record: dict) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def _history_mismatch(expected, actual, history_path) -> str:
    return (f"Incohérence : l'historique ({history_path}) indique que le champion est la v{expected}, "
            f"mais le registre pointe sur {('la v' + str(actual)) if actual is not None else 'aucune version'}. "
            "Si vous avez changé de serveur MLflow, renommez ce fichier d'historique et recommencez ; "
            "sinon l'alias a été modifié à la main.")


def describe_versions(backend: RegistryBackend, name: str, alias: str, policy: dict) -> list:
    """Une ligne par version : verdict, métrique principale (preuve du run), alias en service."""
    metric = policy["primary_metric"]
    current = backend.get_alias(name, alias)
    rows = []
    for v in backend.list_versions(name):
        tags = backend.get_tags(name, v)
        try:
            value = backend.get_evidence(name, v)["metrics"][metric["split"]][metric["name"]]
        except Exception:  # preuve illisible : on l'affiche comme inconnue plutôt que de planter
            value = None
        rows.append({"version": v, "verdict": tags.get("qualification", "?"),
                     "metric": f"{metric['split']}.{metric['name']}", "value": value,
                     "pipeline_run": tags.get("pipeline.run"), "alias": alias if v == current else ""})
    return rows


def alias_stack(history: list[dict], name: str, alias: str) -> list[int]:
    """Rejoue l'historique : select = empiler, rollback = dépiler. Le sommet est la version en service."""
    stack: list[int] = []
    for rec in history:
        if rec["model"] != name or rec["alias"] != alias:
            continue
        if rec["action"] == "select":
            stack.append(rec["version"])
        elif rec["action"] == "rollback" and stack:
            stack.pop()
    return stack


def select_version(backend: RegistryBackend, name: str, version: int, *, policy: dict,
                   expected_corpus_sha: str | None, alias: str, actor: str, reason: str,
                   history_path, dry_run: bool = False) -> dict:
    if not backend.version_exists(name, version):
        raise SelectionRefused(f"Version inconnue : {name} v{version}")
    tags = backend.get_tags(name, version)
    if tags.get("qualification") != PRODUCTION_READY:
        raise SelectionRefused(f"{name} v{version} n'est pas production-ready (tag : {tags.get('qualification')}).")
    if tags.get("qualification.policy") != policy_ref(policy):
        raise SelectionRefused(f"Qualification faite avec une autre politique ({tags.get('qualification.policy')}) "
                               f"que la politique courante ({policy_ref(policy)}) : requalifiez la version.")
    decision = evaluate_version(backend, name, version, policy, expected_corpus_sha)
    if decision.verdict != PRODUCTION_READY:
        raise SelectionRefused(f"Le tag ne correspond pas aux preuves : verdict recalculé = {decision.verdict} "
                               f"({'; '.join(decision.reasons)}).")

    current = backend.get_alias(name, alias)
    stack = alias_stack(_read_history(history_path), name, alias)
    if stack and current != stack[-1]:
        raise SelectionRefused(_history_mismatch(stack[-1], current, history_path))
    if current is not None and current != version:
        metric = policy["primary_metric"]
        key = metric["split"]
        new_v = backend.get_evidence(name, version)["metrics"][key][metric["name"]]
        cur_v = backend.get_evidence(name, current)["metrics"][key][metric["name"]]
        gain = (cur_v - new_v) / cur_v if cur_v else 0.0
        if gain < policy["min_replacement_gain"]:
            raise SelectionRefused(f"Gain de {gain:.1%} sur le champion actuel (v{current}) < "
                                   f"{policy['min_replacement_gain']:.0%} : alias inchangé.")
    record = {"at_utc": now_utc(), "action": "select", "model": name, "alias": alias, "version": version,
              "previous": current, "actor": actor, "reason": reason, "policy": policy_ref(policy)}
    if not dry_run:
        backend.set_alias(name, alias, version)
        _append_history(history_path, record)
    return record


def rollback(backend: RegistryBackend, name: str, *, alias: str, actor: str, history_path) -> dict:
    stack = alias_stack(_read_history(history_path), name, alias)
    registry_now = backend.get_alias(name, alias)
    if stack and registry_now != stack[-1]:
        raise RegistryError(_history_mismatch(stack[-1], registry_now, history_path))
    if len(stack) < 2:
        raise RegistryError(f"Aucune version précédente pour l'alias « {alias} » : retour arrière impossible.")
    current, target = stack[-1], stack[-2]
    if not backend.version_exists(name, target):
        raise RegistryError(f"La version précédente v{target} n'existe plus.")
    if backend.get_tags(name, target).get("qualification") in (REJECTED, DEMO_ONLY):
        raise RegistryError(f"La version précédente v{target} n'est plus admissible.")
    backend.set_alias(name, alias, target)
    record = {"at_utc": now_utc(), "action": "rollback", "model": name, "alias": alias, "version": target,
              "previous": current, "actor": actor, "reason": f"retour arrière de v{current} vers v{target}"}
    _append_history(history_path, record)
    return record
