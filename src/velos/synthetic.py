"""Données SYNTHÉTIQUES au format exact de hour.csv, pour les tests, la CI et le modèle factice.

Ce ne sont PAS les données UCI : tout modèle entraîné dessus est marqué `demo-only` par la politique.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .contract import RAW_COLUMNS


def make_hour_frame(start="2011-01-01", end="2012-12-31", seed: int = 0, drop_fraction: float = 0.01) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    days = pd.date_range(start, end, freq="D")
    dates = np.repeat(days.values, 24)
    hr = np.tile(np.arange(24), len(days))
    d = pd.DatetimeIndex(dates)
    month = d.month.to_numpy()
    year = (d.year - 2011).to_numpy()
    weekday = ((d.day_of_week.to_numpy() + 1) % 7)  # 0 = dimanche, comme dans UCI
    holiday = (rng.random(len(d)) < 0.02).astype(int)
    workingday = ((weekday > 0) & (weekday < 6) & (holiday == 0)).astype(int)
    season = ((month % 12) // 3 + 1)
    weathersit = rng.choice([1, 2, 3], size=len(d), p=[0.65, 0.25, 0.10])
    base_temp = 0.5 - 0.35 * np.cos((month - 1) / 12 * 2 * np.pi)
    temp = np.clip(base_temp + 0.12 * np.sin((hr - 9) / 24 * 2 * np.pi) + rng.normal(0, 0.05, len(d)), 0.02, 0.98)
    atemp = np.clip(temp * 0.95 + rng.normal(0, 0.02, len(d)), 0.02, 0.98)
    hum = np.clip(rng.normal(0.6, 0.15, len(d)), 0.1, 1.0)
    windspeed = np.clip(rng.normal(0.2, 0.1, len(d)), 0.0, 0.8)
    commute = np.exp(-((hr - 8) ** 2) / 4) + np.exp(-((hr - 17.5) ** 2) / 6)
    leisure = np.exp(-((hr - 14) ** 2) / 18)
    rate = (60 + 180 * np.where(workingday == 1, commute, leisure)) * (0.5 + temp) * (1 + 0.6 * year)
    rate = rate * np.where(weathersit == 3, 0.45, np.where(weathersit == 2, 0.85, 1.0)) * (1 - 0.3 * windspeed)
    cnt = np.maximum(rng.poisson(np.maximum(rate, 1)), 1)
    casual = np.round(cnt * np.where(workingday == 1, 0.15, 0.4)).astype(int)
    df = pd.DataFrame({
        "instant": np.arange(1, len(d) + 1), "dteday": d.strftime("%Y-%m-%d"),
        "season": season, "yr": year, "mnth": month, "hr": hr, "holiday": holiday, "weekday": weekday,
        "workingday": workingday, "weathersit": weathersit,
        "temp": temp.round(2), "atemp": atemp.round(4), "hum": hum.round(2), "windspeed": windspeed.round(4),
        "casual": casual, "registered": cnt - casual, "cnt": cnt,
    })[RAW_COLUMNS]
    if drop_fraction:
        keep = rng.random(len(df)) >= drop_fraction
        df = df[keep].reset_index(drop=True)
    return df
