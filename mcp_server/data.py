"""Loading the gold tables of project 2 and the 'replay' logic.

The Olist data ends in August 2018. To run the weekly report as if it were a real Monday, every tool takes an
`as_of` date: the report then covers the last COMPLETE week before that date (Monday as_of → previous Mon–Sun),
exactly what a team would see on that morning. Without `as_of`, the latest complete week in the data is used.
"""
from __future__ import annotations

import os
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

import pandas as pd

DATA_DIR = Path(os.getenv("KPI_DATA_DIR", Path(__file__).resolve().parent.parent / "data"))


def _path(name: str) -> Path:
    return DATA_DIR / f"{name}.parquet"


@lru_cache(maxsize=1)
def weekly() -> pd.DataFrame:
    """Weekly KPIs of the analysis period (Jan 2017 – Aug 2018), one row per Monday."""
    df = pd.read_parquet(_path("gold_weekly_kpis"))
    if "is_analysis_period" in df:
        df = df[df["is_analysis_period"].astype(bool)]
    df = df.copy()
    df["week_start"] = pd.to_datetime(df["week_start"]).dt.date
    for col in df.columns:
        if col != "week_start" and df[col].dtype == object:  # Spark decimals arrive as objects
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.sort_values("week_start").reset_index(drop=True)


@lru_cache(maxsize=1)
def scorecard() -> pd.DataFrame:
    df = pd.read_parquet(_path("gold_supplier_scorecard")).copy()
    df["month"] = pd.to_datetime(df["month"]).dt.date
    df["revenue_eur"] = pd.to_numeric(df["revenue_eur"], errors="coerce")
    return df


def dq_results() -> pd.DataFrame | None:
    """DQ history is re-read on every call: the daily job of project 2 appends a new run each day."""
    path = _path("dq_results")
    if not path.exists():
        return None
    df = pd.read_parquet(path)
    df["run_ts"] = pd.to_datetime(df["run_ts"])
    return df


def is_complete(df: pd.DataFrame, idx: int) -> bool:
    """A week is complete if it has at least half the median orders of the 8 weeks before it.
    The last Olist weeks are cut off (130 orders instead of ~1,800) and must not trigger alarms."""
    prev = df["orders"].iloc[max(0, idx - 8) : idx]
    return prev.empty or df["orders"].iloc[idx] >= 0.5 * prev.median()


def report_index(as_of: str | None) -> int:
    """Row index of the week to report on."""
    df = weekly()
    if as_of:
        cutoff = date.fromisoformat(as_of)
        idx_list = [i for i, ws in enumerate(df["week_start"]) if ws + timedelta(days=7) <= cutoff]
        if not idx_list:
            raise ValueError(f"No complete week before {as_of}. Data starts {df['week_start'].iloc[0]}.")
        idx = idx_list[-1]
    else:
        idx = len(df) - 1
    while idx > 0 and not is_complete(df, idx):  # skip cut-off weeks at the end of the data
        idx -= 1
    return idx


def calendar_week(d: date) -> str:
    iso = d.isocalendar()
    return f"KW {iso.week}/{iso.year}"
