"""Batch ETL: extract CSV -> validate (quarantine bad rows) -> transform -> CDC upsert into SQLite.

The same contract is implemented at scale in `spark_job.py` (PySpark) and `scala/SalesEtl.scala`.
"""
from __future__ import annotations

import hashlib
import sqlite3
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

REQUIRED = ["order_id", "order_date", "region", "product", "units", "unit_price"]

DDL = """
CREATE TABLE IF NOT EXISTS sales (
    order_id TEXT PRIMARY KEY, order_date TEXT NOT NULL, month TEXT NOT NULL,
    region TEXT NOT NULL, product TEXT NOT NULL, units INTEGER NOT NULL,
    unit_price REAL NOT NULL, revenue REAL NOT NULL, row_hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sales_quarantine (
    order_id TEXT, reason TEXT, raw TEXT, loaded_at REAL
);
CREATE TABLE IF NOT EXISTS etl_runs (
    run_at REAL, source TEXT, rows_in INTEGER, inserted INTEGER, updated INTEGER,
    unchanged INTEGER, quarantined INTEGER, seconds REAL
);
CREATE INDEX IF NOT EXISTS idx_sales_region ON sales(region);
"""


@dataclass
class RunReport:
    rows_in: int
    inserted: int
    updated: int
    unchanged: int
    quarantined: int
    seconds: float

    def to_dict(self) -> dict:
        return asdict(self)


def generate_sample_csv(path: Path, rows: int = 9600, seed: int = 42, end: date | None = None) -> Path:
    """Two years of order history (so period-over-period comparisons work) with steady growth,
    a holiday peak and uneven regions/products - about 400 orders a month."""
    rng = np.random.default_rng(seed)
    end = end or datetime.now(timezone.utc).astimezone().date()  # the server's local date
    days = pd.date_range(end=pd.Timestamp(end), periods=730, freq="D")
    weight = np.linspace(1.0, 2.6, len(days)) * np.where(days.month.isin([11, 12]), 1.25, 1.0)
    df = pd.DataFrame({
        "order_id": [f"ORD-{i:05d}" for i in range(rows)],
        "order_date": rng.choice(days.values, rows, p=weight / weight.sum()),
        "region": rng.choice(["North", "South", "East", "West"], rows, p=[.32, .22, .28, .18]),
        "product": rng.choice(["Atlas", "Beacon", "Cipher", "Delta"], rows, p=[.4, .3, .2, .1]),
        "units": rng.integers(1, 40, rows),
        "unit_price": rng.choice([19.99, 49.5, 99.0, 249.0], rows),
    })
    df["order_date"] = pd.to_datetime(df["order_date"]).dt.strftime("%Y-%m-%d")
    # inject realistic dirt so data-quality rules are exercised
    df.loc[3, "units"] = -5
    df.loc[7, "region"] = None
    df.loc[11, "order_date"] = "not-a-date"
    df = pd.concat([df, df.iloc[[20]]], ignore_index=True)  # duplicate key
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)
    return path


def validate(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    missing = [c for c in REQUIRED if c not in df.columns]
    if missing:
        raise ValueError(f"missing required columns: {missing}")
    df = df.copy()
    df["_reason"] = ""

    def flag(mask: pd.Series, reason: str) -> None:
        df.loc[mask & (df["_reason"] == ""), "_reason"] = reason

    flag(df[REQUIRED].isna().any(axis=1), "null_required_field")
    dates = pd.to_datetime(df["order_date"], errors="coerce")
    flag(dates.isna(), "invalid_date")
    units = pd.to_numeric(df["units"], errors="coerce")
    price = pd.to_numeric(df["unit_price"], errors="coerce")
    flag(units.isna() | (units <= 0), "non_positive_units")
    flag(price.isna() | (price <= 0), "non_positive_price")
    flag(df.duplicated("order_id", keep="first"), "duplicate_order_id")
    bad = df["_reason"] != ""
    return df[~bad].drop(columns="_reason"), df[bad]


def transform(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["order_date"] = pd.to_datetime(out["order_date"]).dt.strftime("%Y-%m-%d")
    out["month"] = out["order_date"].str.slice(0, 7)
    out["units"] = out["units"].astype(int)
    out["unit_price"] = out["unit_price"].astype(float)
    out["revenue"] = (out["units"] * out["unit_price"]).round(2)
    for c in ("region", "product"):
        out[c] = out[c].astype(str).str.strip().str.title()
    cols = ["order_id", "order_date", "month", "region", "product", "units", "unit_price", "revenue"]
    out = out[cols]
    out["row_hash"] = out.astype(str).agg("|".join, axis=1).map(lambda s: hashlib.sha256(s.encode()).hexdigest())
    return out


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path)
    con.executescript(DDL)
    return con


def run_etl(csv_path: Path, db_path: Path) -> RunReport:
    return run_etl_frame(pd.read_csv(csv_path, dtype=str), db_path, str(csv_path))


def run_etl_frame(raw: pd.DataFrame, db_path: Path, source: str = "frame") -> RunReport:
    t0 = time.perf_counter()
    good, bad = validate(raw)
    clean = transform(good)
    con = connect(db_path)
    try:
        existing = dict(con.execute("SELECT order_id, row_hash FROM sales"))
        ins = upd = same = 0
        rows = []
        for r in clean.itertuples(index=False):
            old = existing.get(r.order_id)
            if old is None:
                ins += 1
            elif old != r.row_hash:
                upd += 1
            else:
                same += 1
                continue
            rows.append(tuple(r))
        with con:  # single atomic transaction
            con.executemany(
                "INSERT INTO sales VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(order_id) DO UPDATE SET "
                "order_date=excluded.order_date, month=excluded.month, region=excluded.region, "
                "product=excluded.product, units=excluded.units, unit_price=excluded.unit_price, "
                "revenue=excluded.revenue, row_hash=excluded.row_hash", rows)
            now = time.time()
            con.executemany(
                "INSERT INTO sales_quarantine VALUES (?,?,?,?)",
                [(str(r.order_id), r._reason, r.drop("_reason").to_json(), now) for _, r in bad.iterrows()])
            report = RunReport(len(raw), ins, upd, same, len(bad), round(time.perf_counter() - t0, 3))
            con.execute("INSERT INTO etl_runs VALUES (?,?,?,?,?,?,?,?)",
                        (now, source, report.rows_in, ins, upd, same, report.quarantined, report.seconds))
        return report
    finally:
        con.close()
