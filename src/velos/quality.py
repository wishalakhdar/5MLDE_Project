"""Contrôles qualité BLOQUANTS, exécutés avant tout prétraitement (A3).

Choix technique (à justifier dans la synthèse) : règles écrites en pandas pur, sans Great Expectations.
Raison : aucune dépendance supplémentaire, comportement testable sans serveur, et le même code
valide le snapshot, les lots entrants et le corpus de qualification.

Un échec ne se contente pas d'écrire un rapport : `enforce` LÈVE une exception, donc le pipeline s'arrête.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from .contract import INTEGER_FEATURES, RANGES, RAW_COLUMNS
from .errors import QualityError

NUMERIC_COLUMNS = [c for c in RAW_COLUMNS if c != "dteday"]
MAX_EXAMPLES = 5
LOT_NAME = re.compile(r"^lot-(\d{4}-\d{2})")
KINDS = ("snapshot", "lot", "corpus")


@dataclass
class Failure:
    rule: str
    message: str
    count: int = 0
    examples: list = field(default_factory=list)


@dataclass
class QualityReport:
    kind: str
    source: str
    rows: int
    sha256: str
    checked_at: str
    rules_run: list
    failures: list

    @property
    def passed(self) -> bool:
        return not self.failures

    def summary(self) -> str:
        if self.passed:
            return f"Contrôles qualité réussis ({self.kind}, {self.rows} lignes)."
        rules = ", ".join(dict.fromkeys(f.rule for f in self.failures))
        return f"Contrôles qualité ÉCHOUÉS ({self.kind}, {self.source}) : {rules}"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["passed"] = self.passed
        return d

    def write(self, path) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        return p


def _examples(df: pd.DataFrame, mask: pd.Series) -> list:
    sel = df.loc[mask]
    key = sel["instant"] if "instant" in sel.columns else sel.index.to_series()
    return [v.item() if hasattr(v, "item") else v for v in key.head(MAX_EXAMPLES).tolist()]


def validate(df: pd.DataFrame, kind: str, cfg: dict, *, reference: pd.DataFrame | None = None,
             lot_name: str | None = None, source: str = "", sha256: str = "") -> QualityReport:
    """Applique toutes les règles. Ne lève rien : retourne un rapport (voir `enforce`)."""
    if kind not in KINDS:
        raise ValueError(f"kind doit être l'un de {KINDS}")
    q = cfg["quality"]
    failures: list[Failure] = []
    rules: list[str] = []

    def done(report_failures=True) -> QualityReport:
        return QualityReport(kind, source, int(len(df)), sha256,
                             datetime.now(timezone.utc).isoformat(), rules, failures)

    # 1. Structure
    rules.append("structure")
    missing = [c for c in RAW_COLUMNS if c not in df.columns]
    if missing:
        failures.append(Failure("structure", f"colonnes manquantes : {missing}", len(missing), missing))
        return done()  # impossible d'aller plus loin

    # 2. Valeurs manquantes
    rules.append("valeurs_manquantes")
    nulls = df[RAW_COLUMNS].isna().sum()
    nulls = nulls[nulls > 0]
    if len(nulls):
        failures.append(Failure("valeurs_manquantes", f"valeurs manquantes dans : {list(nulls.index)}",
                                int(nulls.sum()), list(nulls.index)))

    # 3. Types
    rules.append("types")
    num = df[NUMERIC_COLUMNS].apply(pd.to_numeric, errors="coerce")
    for c in NUMERIC_COLUMNS:
        bad = num[c].isna() & df[c].notna()
        if bad.any():
            failures.append(Failure("types", f"valeurs non numériques dans {c}", int(bad.sum()), _examples(df, bad)))
    dates = pd.to_datetime(df["dteday"], format="%Y-%m-%d", errors="coerce")
    bad_dates = dates.isna() & df["dteday"].notna()
    if bad_dates.any():
        failures.append(Failure("types", "dates dteday illisibles (format AAAA-MM-JJ attendu)",
                                int(bad_dates.sum()), _examples(df, bad_dates)))

    # 4. Plages de valeurs
    rules.append("plages")
    for c, (lo, hi) in RANGES.items():
        out = num[c].notna() & ~num[c].between(lo, hi)
        if out.any():
            failures.append(Failure("plages", f"{c} hors de [{lo}, {hi}]", int(out.sum()), _examples(df, out)))
        if c in INTEGER_FEATURES:
            frac = num[c].notna() & (num[c] % 1 != 0)
            if frac.any():
                failures.append(Failure("plages", f"{c} doit être entier", int(frac.sum()), _examples(df, frac)))
    for c in ("cnt", "casual", "registered"):
        neg = num[c].notna() & ((num[c] < 0) | (num[c] % 1 != 0))
        if neg.any():
            failures.append(Failure("plages", f"{c} doit être un entier >= 0", int(neg.sum()), _examples(df, neg)))

    # 5. Cohérence interne : cnt = casual + registered
    rules.append("coherence_cnt")
    ok = num[["cnt", "casual", "registered"]].notna().all(axis=1)
    incoherent = ok & (num["cnt"] != num["casual"] + num["registered"])
    if incoherent.any():
        failures.append(Failure("coherence_cnt", "cnt != casual + registered", int(incoherent.sum()),
                                _examples(df, incoherent)))

    # 6. Unicité
    rules.append("unicite")
    dup_instant = num["instant"].notna() & num["instant"].duplicated(keep=False)
    if dup_instant.any():
        failures.append(Failure("unicite", "instant dupliqué", int(dup_instant.sum()), _examples(df, dup_instant)))
    dup_slot = df.duplicated(subset=["dteday", "hr"], keep=False)
    if dup_slot.any():
        failures.append(Failure("unicite", "couple (dteday, hr) dupliqué", int(dup_slot.sum()),
                                _examples(df, dup_slot)))

    # 7. Cohérence des dates
    rules.append("coherence_dates")
    valid = dates.notna() & num["yr"].notna() & num["mnth"].notna()
    bad_yr = valid & (num["yr"] != dates.dt.year - 2011)
    bad_mn = valid & (num["mnth"] != dates.dt.month)
    for mask, text in [(bad_yr, "yr incohérent avec dteday"), (bad_mn, "mnth incohérent avec dteday")]:
        if mask.any():
            failures.append(Failure("coherence_dates", text, int(mask.sum()), _examples(df, mask)))

    # 8. Volume
    rules.append("volume")
    n = len(df)
    lo_n, hi_n = (q["snapshot_min_rows"], None) if kind == "snapshot" else (q["lot_min_rows"], q["lot_max_rows"])
    if n < lo_n or (hi_n is not None and n > hi_n):
        bounds = f">= {lo_n}" if hi_n is None else f"entre {lo_n} et {hi_n}"
        failures.append(Failure("volume", f"{n} lignes, attendu {bounds}", n))

    # 9. Représentativité
    if kind in ("lot", "corpus"):
        rules.append("representativite")
        hours = set(num["hr"].dropna().astype(int).tolist()) if num["hr"].notna().any() else set()
        if len(hours) < q["min_hours_covered"]:
            missing_hours = sorted(set(range(24)) - hours)
            failures.append(Failure("representativite", f"heures absentes : {missing_hours}", len(missing_hours),
                                    missing_hours))
        if num["weathersit"].nunique() < 2:
            failures.append(Failure("representativite", "une seule valeur de weathersit", n))
        if kind == "lot" and reference is not None and len(reference):
            ref_mean = pd.to_numeric(reference["cnt"], errors="coerce").mean()
            lot_mean = num["cnt"].mean()
            lo_r, hi_r = q["mean_cnt_ratio_bounds"]
            if ref_mean and lot_mean == lot_mean:
                ratio = lot_mean / ref_mean
                if not lo_r <= ratio <= hi_r:
                    failures.append(Failure("representativite",
                                            f"moyenne de cnt = {ratio:.2f} x celle du snapshot (attendu {lo_r}-{hi_r})",
                                            n, [round(float(ratio), 3)]))

    # 10. Continuité d'un lot avec le snapshot existant
    if kind == "lot":
        rules.append("continuite_lot")
        months = dates.dropna().dt.strftime("%Y-%m").unique().tolist()
        if len(months) > 1:
            failures.append(Failure("continuite_lot", f"le lot couvre plusieurs mois : {sorted(months)}", len(months)))
        match = LOT_NAME.match(lot_name or "")
        if not match:
            failures.append(Failure("continuite_lot", "nom de lot attendu : lot-AAAA-MM[...]", 1, [lot_name]))
        elif months and months != [match.group(1)]:
            failures.append(Failure("continuite_lot", f"le contenu ({months}) ne correspond pas au nom ({lot_name})", 1))
        if reference is not None and len(reference) and dates.notna().any():
            ref_dates = pd.to_datetime(reference["dteday"], format="%Y-%m-%d", errors="coerce")
            if dates.min() <= ref_dates.max():
                failures.append(Failure("continuite_lot",
                                        f"le lot commence le {dates.min().date()} mais le snapshot va jusqu'au {ref_dates.max().date()}",
                                        1))
            shared = set(num["instant"].dropna().tolist()) & set(pd.to_numeric(reference["instant"]).tolist())
            if shared:
                failures.append(Failure("continuite_lot", "instants déjà présents dans le snapshot", len(shared),
                                        sorted(shared)[:MAX_EXAMPLES]))
    return done()


def enforce(report: QualityReport, report_path=None) -> QualityReport:
    """Écrit le rapport (même en cas d'échec) puis LÈVE QualityError s'il y a une anomalie."""
    if report_path is not None:
        report.write(report_path)
    if not report.passed:
        raise QualityError(report)
    return report
