"""Ligne de commande `velos`. Codes de sortie : 0 ok, 2 lot rejeté par la qualité, 3 autre erreur."""
from __future__ import annotations

import argparse
import getpass
import json
import sys

from .config import load_config, path_of
from .errors import AlreadyProcessed, ConfigError, QualityError, VelosError


def _p(msg: str) -> None:
    print(msg, flush=True)


def default_actor() -> str:
    """Auteur d'une sélection ou d'un retour arrière : --actor, sinon VELOS_ACTOR, sinon l'utilisateur du système
    (traçabilité : jamais un nom de personne écrit en dur)."""
    import os
    return os.environ.get("VELOS_ACTOR") or getpass.getuser()


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="velos", description="Pipeline MLOps Vélos (UCI Bike Sharing)")
    ap.add_argument("--config", default="config.json")
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch", help="télécharge hour.csv (UCI) et vérifie l'empreinte")
    f.add_argument("--from-file", help="utiliser un CSV local au lieu du téléchargement")
    sub.add_parser("replay-init", help="découpe le jeu en base / lots / corpus de qualification")
    sub.add_parser("replay-next", help="dépose le prochain lot dans data/incoming")
    b = sub.add_parser("make-bad-lot", help="INCIDENT : dépose une version invalide du prochain lot")
    b.add_argument("--kind", default="negatif", choices=["negatif", "colonne", "volume"])
    r = sub.add_parser("run", help="pipeline local (sans Prefect)")
    r.add_argument("--lot")
    r.add_argument("--no-tracking", action="store_true")
    r.add_argument("--no-release", action="store_true")
    fl = sub.add_parser("flow", help="un lot via Prefect")
    fl.add_argument("--lot")
    sub.add_parser("serve-flows", help="surveillance planifiée de data/incoming (Prefect)")
    q = sub.add_parser("qualify", help="(re)qualifie une version du registre")
    q.add_argument("--version", type=int, required=True)
    s = sub.add_parser("select", help="pose l'alias champion (après contrôles)")
    s.add_argument("--version", type=int, required=True)
    s.add_argument("--actor", default=None)
    s.add_argument("--reason", required=True)
    s.add_argument("--dry-run", action="store_true")
    rb = sub.add_parser("rollback", help="revient au champion précédent")
    rb.add_argument("--actor", default=None)
    vr = sub.add_parser("verify-reload", help="(interne) recharge une version dans un nouveau processus")
    vr.add_argument("--run-dir", required=True)
    vr.add_argument("--name", required=True)
    vr.add_argument("--version", type=int, required=True)
    sub.add_parser("versions", help="liste les versions du registre (verdict, métrique, alias)")
    pr = sub.add_parser("predict", help="prédit avec un run publié OU avec le modèle désigné par un alias")
    pr.add_argument("--run-dir", help="dossier d'un run publié")
    pr.add_argument("--alias", help="alias du registre (ex. champion) : chargement models:/<nom>@<alias>")
    pr.add_argument("--input", required=True)
    pr.add_argument("--output", required=True)
    sub.add_parser("stub-model", help="modèle de démonstration pour Jeffrey (données synthétiques)")
    return ap


def _expected_corpus(cfg):
    from .state import read_store_manifest
    return read_store_manifest(cfg)["files"]["qualification.csv"]["sha256"]


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if getattr(args, "actor", "unset") is None:
        args.actor = default_actor()
    try:
        cfg = load_config(args.config)
        return _dispatch(args, cfg)
    except QualityError as exc:
        _p(f"LOT REJETÉ : {exc.report.summary()}")
        return 2
    except AlreadyProcessed as exc:
        _p(str(exc))
        return 0
    except VelosError as exc:
        _p(f"ERREUR : {exc}")
        return 3


def _dispatch(args, cfg) -> int:
    c = args.cmd
    if c == "fetch":
        from .ingestion import fetch
        _p(json.dumps(fetch(cfg, from_file=args.from_file), indent=2, ensure_ascii=False))
    elif c == "replay-init":
        from .replay import init_store
        _p(json.dumps(init_store(cfg), indent=2, ensure_ascii=False))
    elif c == "replay-next":
        from .replay import next_lot
        _p(str(next_lot(cfg) or "Plus aucun lot à rejouer."))
    elif c == "make-bad-lot":
        from .replay import make_bad_lot
        _p(str(make_bad_lot(cfg, args.kind) or "Plus aucun lot."))
    elif c == "run":
        from .pipeline import commit_lot, run_pipeline
        from .tracking import NullTracker, make_tracker
        tracker = NullTracker() if args.no_tracking else make_tracker(cfg)
        res = run_pipeline(cfg, args.lot, tracker)
        _p(f"Run publié : {res.run_dir}")
        if not args.no_release:
            from .registry import make_backend
            from .release import release_model
            _p(json.dumps(release_model(cfg, res.run_dir, make_backend(cfg)), indent=2, ensure_ascii=False))
        commit_lot(cfg, res)
    elif c == "flow":
        from .flows import process_lot_flow
        _p(json.dumps(process_lot_flow(cfg["_config_path"], args.lot), indent=2, ensure_ascii=False))
    elif c == "serve-flows":
        from .flows import serve_flows
        serve_flows(cfg["_config_path"])
    elif c == "qualify":
        from .lifecycle import qualify_version
        from .qualification import load_policy
        from .registry import make_backend
        d = qualify_version(make_backend(cfg), cfg["tracking"]["model_name"], args.version, load_policy(cfg),
                            _expected_corpus(cfg), report_dir=path_of(cfg, "reports_dir"))
        _p(f"{d.verdict} : {'; '.join(d.reasons)}")
    elif c == "select":
        from .lifecycle import select_version
        from .qualification import load_policy
        from .registry import make_backend
        rec = select_version(make_backend(cfg), cfg["tracking"]["model_name"], args.version,
                             policy=load_policy(cfg), expected_corpus_sha=_expected_corpus(cfg),
                             alias=cfg["qualification"]["alias"], actor=args.actor, reason=args.reason,
                             history_path=path_of(cfg, "history_file"), dry_run=args.dry_run)
        _p(json.dumps(rec, indent=2, ensure_ascii=False))
    elif c == "rollback":
        from .lifecycle import rollback
        from .registry import make_backend
        rec = rollback(make_backend(cfg), cfg["tracking"]["model_name"], alias=cfg["qualification"]["alias"],
                       actor=args.actor, history_path=path_of(cfg, "history_file"))
        _p(json.dumps(rec, indent=2, ensure_ascii=False))
    elif c == "verify-reload":
        from .release import verify_reload
        _p(json.dumps(verify_reload(cfg, args.run_dir, args.name, args.version)))
    elif c == "versions":
        from .lifecycle import describe_versions
        from .qualification import load_policy
        from .registry import make_backend
        rows = describe_versions(make_backend(cfg), cfg["tracking"]["model_name"], cfg["qualification"]["alias"],
                                 load_policy(cfg))
        if not rows:
            _p("Aucune version dans le registre.")
        for r in rows:
            val = "?" if r["value"] is None else f"{r['value']:.2f}"
            _p(f"v{r['version']:<3} {r['verdict']:<17} {r['metric']}={val:<8} {r['pipeline_run'] or '':<45} {r['alias']}")
    elif c == "predict":
        if bool(args.run_dir) == bool(args.alias):
            raise ConfigError("Indiquez exactement une option : --run-dir ou --alias.")
        if args.alias:
            from .predict import predict_file_alias
            out, uri, version = predict_file_alias(cfg, args.input, args.output, args.alias)
            _p(f"Modèle chargé par alias : {uri} (version {version})")
            _p(f"Prédictions écrites : {out}")
        else:
            from .predict import predict_file
            _p(f"Prédictions écrites : {predict_file(args.run_dir, args.input, args.output)}")
    elif c == "stub-model":
        return _stub_model(cfg)
    return 0


def _stub_model(cfg) -> int:
    """Fabrique un modèle de démonstration (données SYNTHÉTIQUES) pour que Jeffrey démarre l'API sans attendre."""
    import joblib
    from .contract import FEATURES, TARGET
    from .synthetic import make_hour_frame
    from .training import build_pipeline
    df = make_hour_frame(seed=cfg["seed"])
    model = build_pipeline({**cfg["model"], "n_estimators": 20}, cfg["seed"]).fit(df[FEATURES], df[TARGET])
    out = path_of(cfg, "runs_dir") / "stub-model"
    out.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, out / "model.joblib")
    (out / "README.txt").write_text("MODÈLE DE DÉMONSTRATION (données synthétiques). Ne jamais présenter comme réel.\n",
                                    encoding="utf-8")
    _p(f"Modèle de démonstration : {out / 'model.joblib'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
