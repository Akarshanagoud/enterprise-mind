"""Warehouse analytics for the console: KPIs, trends, breakdowns, pipeline runs and data quality.

All reads use a read-only SQLite connection and bound parameters; filters are validated first.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

PERIODS = {"12m": "Last 12 months", "6m": "Last 6 months", "90d": "Last 90 days",
           "ytd": "Year to date", "all": "All time"}


def local_today() -> date:
    """The server's local calendar date, derived from an aware timestamp."""
    return datetime.now(timezone.utc).astimezone().date()


def _ro(db: Path) -> sqlite3.Connection | None:
    db = Path(db)
    if not db.exists():
        return None
    return sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)


def _tables(con: sqlite3.Connection) -> set[str]:
    return {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _month_start(d: date, back: int = 0) -> date:
    y, m = d.year, d.month - back
    while m <= 0:
        m, y = m + 12, y - 1
    return date(y, m, 1)


def period_bounds(period: str, today: date) -> tuple[date | None, date]:
    if period == "12m":
        return _month_start(today, 11), today
    if period == "6m":
        return _month_start(today, 5), today
    if period == "90d":
        return today - timedelta(days=89), today
    if period == "ytd":
        return date(today.year, 1, 1), today
    if period == "all":
        return None, today
    raise ValueError(f"unknown period '{period}'; expected one of {sorted(PERIODS)}")


def _months(first: str, last: str) -> list[str]:
    y, m = map(int, first.split("-"))
    ly, lm = map(int, last.split("-"))
    out = []
    while (y, m) <= (ly, lm):
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def summary(db: Path, period: str = "12m", region: str | None = None, today: date | None = None) -> dict:
    today = today or local_today()
    start, end = period_bounds(period, today)
    base = {"period": period, "period_label": PERIODS[period], "region": region or None,
            "start": start.isoformat() if start else None, "end": end.isoformat()}
    con = _ro(db)
    if con is None:
        return {**base, "empty": True, "regions": []}
    try:
        tables = _tables(con)
        if "sales" not in tables or not con.execute("SELECT 1 FROM sales LIMIT 1").fetchone():
            return {**base, "empty": True, "regions": []}
        regions = [r[0] for r in con.execute("SELECT DISTINCT region FROM sales ORDER BY region")]
        if region and region not in regions:
            raise ValueError(f"unknown region '{region}'")

        def where(s: date | None, e: date) -> tuple[str, list]:
            clauses, params = ["order_date <= ?"], [e.isoformat()]
            if s:
                clauses.append("order_date >= ?")
                params.append(s.isoformat())
            if region:
                clauses.append("region = ?")
                params.append(region)
            return " WHERE " + " AND ".join(clauses), params

        def totals(s: date | None, e: date) -> dict:
            w, p = where(s, e)
            rev, orders, units = con.execute(
                f"SELECT COALESCE(SUM(revenue),0), COUNT(*), COALESCE(SUM(units),0) FROM sales{w}", p).fetchone()
            return {"revenue": round(rev, 2), "orders": orders, "units": units,
                    "aov": round(rev / orders, 2) if orders else 0.0}

        kpis = totals(start, end)
        previous = None
        data_start = con.execute("SELECT MIN(order_date) FROM sales").fetchone()[0]
        if start:  # compare only against a prior window the data fully covers
            prev_end = start - timedelta(days=1)
            prev_start = prev_end - (end - start)
            if data_start and data_start <= prev_start.isoformat():
                previous = totals(prev_start, prev_end)

        w, p = where(start, end)
        monthly = dict(con.execute(
            f"SELECT month, ROUND(SUM(revenue),2) FROM sales{w} GROUP BY month ORDER BY month", p).fetchall())
        current = today.strftime("%Y-%m")
        months = _months(start.strftime("%Y-%m") if start else min(monthly), current) if monthly else []
        def breakdown(dim: str) -> list[dict]:  # dim is one of two literals below, never user input
            sql = f"SELECT {dim}, ROUND(SUM(revenue),2) AS v FROM sales{w} GROUP BY {dim} ORDER BY v DESC"
            return [{"label": k, "value": v} for k, v in con.execute(sql, p)]

        last_load = con.execute("SELECT MAX(run_at) FROM etl_runs").fetchone()[0] if "etl_runs" in tables else None
        return {
            **base, "empty": False, "regions": regions, "kpis": kpis, "previous": previous,
            "monthly": [{"month": m, "revenue": monthly.get(m, 0.0), "partial": m == current} for m in months],
            "by_region": breakdown("region"), "by_product": breakdown("product"), "last_load": last_load,
        }
    finally:
        con.close()


def etl_runs(db: Path, n: int = 20) -> list[dict]:
    con = _ro(db)
    if con is None:
        return []
    try:
        if "etl_runs" not in _tables(con):
            return []
        cur = con.execute("SELECT * FROM etl_runs ORDER BY run_at DESC LIMIT ?", (n,))
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
    finally:
        con.close()


def quarantine(db: Path, n: int = 25) -> dict:
    con = _ro(db)
    if con is None:
        return {"reasons": [], "recent": []}
    try:
        if "sales_quarantine" not in _tables(con):
            return {"reasons": [], "recent": []}
        reasons = [{"label": r, "value": c} for r, c in con.execute(
            "SELECT reason, COUNT(*) AS c FROM sales_quarantine GROUP BY reason ORDER BY c DESC")]
        recent = []
        for oid, reason, raw, ts in con.execute(
                "SELECT order_id, reason, raw, loaded_at FROM sales_quarantine ORDER BY loaded_at DESC LIMIT ?", (n,)):
            try:
                record = json.loads(raw)
            except (TypeError, json.JSONDecodeError):
                record = {}
            recent.append({"order_id": oid, "reason": reason, "record": record, "loaded_at": ts})
        return {"reasons": reasons, "recent": recent}
    finally:
        con.close()
