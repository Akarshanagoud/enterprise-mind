"""Real-time ingestion: micro-batch stream processor with idempotent upserts and tumbling windows.

Works on any iterable of event dicts. `kafka_events` adapts a Kafka topic (free, `pip install
kafka-python`); `file_events` tails a JSONL file so it runs with zero infrastructure.
"""
from __future__ import annotations

import json
import time
from collections.abc import Iterable, Iterator
from pathlib import Path

import pandas as pd

from .etl.pipeline import connect, run_etl_frame

WINDOW_DDL = """CREATE TABLE IF NOT EXISTS stream_windows (
    window_start INTEGER, region TEXT, revenue REAL, orders INTEGER,
    PRIMARY KEY (window_start, region))"""


def file_events(path: Path) -> Iterator[dict]:
    with Path(path).open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    yield {"_malformed": line.strip()}


def kafka_events(topic: str, bootstrap: str = "localhost:9092", group: str = "emind") -> Iterator[dict]:
    from kafka import KafkaConsumer  # type: ignore

    for msg in KafkaConsumer(topic, bootstrap_servers=bootstrap, group_id=group,
                             value_deserializer=lambda b: json.loads(b.decode())):
        yield msg.value


class StreamProcessor:
    def __init__(self, db_path: Path, batch_size: int = 100, window_seconds: int = 3600):
        self.db_path, self.batch_size, self.window = Path(db_path), batch_size, window_seconds

    def process(self, events: Iterable[dict]) -> dict:
        totals = {"events": 0, "batches": 0, "inserted": 0, "updated": 0, "unchanged": 0, "quarantined": 0}
        buf: list[dict] = []

        def flush() -> None:
            if not buf:
                return
            rep = run_etl_frame(pd.DataFrame(buf).astype(object).where(lambda d: d.notna(), None).astype("string"),
                                self.db_path, "stream")
            totals["batches"] += 1
            for k in ("inserted", "updated", "unchanged", "quarantined"):
                totals[k] += getattr(rep, k)
            self._windows(buf)
            buf.clear()

        for ev in events:
            totals["events"] += 1
            if "_malformed" in ev:
                totals["quarantined"] += 1
                continue
            buf.append({k: ev.get(k) for k in ("order_id", "order_date", "region", "product", "units", "unit_price")})
            if len(buf) >= self.batch_size:
                flush()
        flush()
        return totals

    def _windows(self, rows: list[dict]) -> None:
        """Tumbling-window revenue per region, bucketed by ingestion time (processing-time semantics)."""
        start = int(time.time() // self.window * self.window)
        agg: dict = {}
        for r in rows:
            try:
                rev = float(r["units"]) * float(r["unit_price"])
            except (TypeError, ValueError):
                continue
            if rev <= 0 or not r.get("region"):
                continue
            k = str(r["region"]).strip().title()
            a = agg.setdefault(k, [0.0, 0])
            a[0] += rev
            a[1] += 1
        con = connect(self.db_path)
        try:
            with con:
                con.execute(WINDOW_DDL)
                con.executemany(
                    "INSERT INTO stream_windows VALUES (?,?,?,?) ON CONFLICT(window_start, region) DO UPDATE SET "
                    "revenue=revenue+excluded.revenue, orders=orders+excluded.orders",
                    [(start, k, round(v[0], 2), v[1]) for k, v in agg.items()])
        finally:
            con.close()
