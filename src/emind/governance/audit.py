"""Tamper-evident audit log: append-only JSONL where each record hashes the previous one."""
from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path


class AuditLog:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._last = self._load_last_hash()

    def _load_last_hash(self) -> str:
        if not self.path.exists():
            return "0" * 64
        last = "0" * 64
        with self.path.open(encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    last = json.loads(line)["hash"]
        return last

    @staticmethod
    def _digest(prev: str, body: dict) -> str:
        payload = prev + json.dumps(body, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()

    def record(self, event: str, user: str, **details) -> dict:
        with self._lock:
            body = {"ts": round(time.time(), 3), "event": event, "user": user, "details": details}
            rec = {**body, "prev": self._last, "hash": self._digest(self._last, body)}
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec) + "\n")
            self._last = rec["hash"]
            return rec

    def tail(self, n: int | None = 50) -> list[dict]:
        """Most recent `n` records, oldest first (`None` returns the whole log)."""
        if not self.path.exists():
            return []
        with self.path.open(encoding="utf-8") as fh:
            lines = [json.loads(l) for l in fh if l.strip()]
        return lines[-n:] if n else lines

    def verify(self) -> bool:
        prev = "0" * 64
        if not self.path.exists():
            return True
        with self.path.open(encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                rec = json.loads(line)
                body = {k: rec[k] for k in ("ts", "event", "user", "details")}
                if rec["prev"] != prev or rec["hash"] != self._digest(prev, body):
                    return False
                prev = rec["hash"]
        return True
