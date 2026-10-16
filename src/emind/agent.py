"""Agentic workflow: plan -> call tools -> observe -> synthesise, with a full execution trace.

Tools are least-privilege: SQL is read-only, single-statement, table-allowlisted and row-capped.
The planner is rule-based so it works offline; swap `plan()` for an LLM planner without touching tools.
"""
from __future__ import annotations

import re
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .config import Principal
from .rag.pipeline import RAGPipeline

ALLOWED_TABLES = {"sales"}
MAX_ROWS = 100
_FORBIDDEN = re.compile(r"\b(insert|update|delete|drop|alter|create|attach|pragma|replace|vacuum|detach)\b", re.IGNORECASE)


class SQLPolicyError(ValueError):
    pass


def validate_sql(sql: str) -> str:
    s = sql.strip().rstrip(";").strip()
    if ";" in s or "--" in s or "/*" in s:
        raise SQLPolicyError("multiple statements or comments are not allowed")
    if not re.match(r"(?is)^select\b", s):
        raise SQLPolicyError("only SELECT statements are allowed")
    if _FORBIDDEN.search(s):
        raise SQLPolicyError("statement contains a forbidden keyword")
    tables = {t.lower() for t in re.findall(r"(?i)\b(?:from|join)\s+([a-z_][a-z0-9_]*)", s)}
    if not tables or not tables <= ALLOWED_TABLES:
        raise SQLPolicyError(f"table not allowed: {sorted(tables - ALLOWED_TABLES) or 'none referenced'}")
    return s


def run_sql(db_path: Path, sql: str) -> dict:
    s = validate_sql(sql)
    con = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    try:
        cur = con.execute(s)
        cols = [d[0] for d in cur.description]
        rows = cur.fetchmany(MAX_ROWS)
        return {"sql": s, "columns": cols, "rows": [list(r) for r in rows]}
    finally:
        con.close()


_METRIC = {"revenue": "SUM(revenue)", "sales": "SUM(revenue)", "units": "SUM(units)", "orders": "COUNT(*)"}
_DIM = {"region": "region", "product": "product", "month": "month"}


def text_to_sql(question: str) -> str | None:
    """Template NL->SQL over the known schema (deterministic and auditable)."""
    q = question.lower()
    metric = next((v for k, v in _METRIC.items() if k in q), None)
    if metric is None:
        return None
    dim = next((v for k, v in _DIM.items() if re.search(rf"\b{k}s?\b", q)), None)
    if re.search(r"\b(average|avg|mean)\b", q):
        metric = {"SUM(revenue)": "AVG(revenue)", "SUM(units)": "AVG(units)"}.get(metric, metric)
    top = re.search(r"\btop\s+(\d+)\b", q)
    if dim:
        limit = f" LIMIT {min(int(top.group(1)), MAX_ROWS)}" if top else ""
        return (f"SELECT {dim}, ROUND({metric},2) AS value FROM sales GROUP BY {dim} "
                f"ORDER BY value DESC{limit}")
    return f"SELECT ROUND({metric},2) AS value FROM sales"


@dataclass
class Step:
    tool: str
    input: str
    output: object
    ok: bool = True


@dataclass
class AgentResult:
    answer: str
    steps: list[Step] = field(default_factory=list)
    route: str = ""


class Agent:
    def __init__(self, rag: RAGPipeline, warehouse: Path):
        self.rag, self.warehouse = rag, warehouse
        self.tools: dict[str, Callable] = {"sql": self._sql, "search_docs": self._search}

    def _sql(self, question: str, principal: Principal):
        if not principal.can("analyst", "admin"):
            raise PermissionError("role cannot query the warehouse")
        sql = text_to_sql(question)
        if sql is None:
            raise ValueError("question does not map to a known metric")
        return run_sql(self.warehouse, sql)

    def _search(self, question: str, principal: Principal):
        return self.rag.ask(question, principal)

    def plan(self, question: str) -> list[str]:
        q = question.lower()
        numeric = bool(re.search(r"\b(total|sum|average|avg|top|how many|count|revenue|units|sales|orders)\b", q))
        policy = bool(re.search(r"\b(policy|procedure|process|why|explain|guideline|rule|define|what is)\b", q))
        if numeric and policy:
            return ["sql", "search_docs"]
        return ["sql"] if numeric else ["search_docs"]

    def run(self, question: str, principal: Principal) -> AgentResult:
        route = self.plan(question)
        res = AgentResult("", route=" -> ".join(route))
        parts: list[str] = []
        for name in route:
            try:
                out = self.tools[name](question, principal)
                res.steps.append(Step(name, question, out))
                if name == "sql":
                    rows = out["rows"]
                    parts.append("Warehouse result: " + "; ".join(", ".join(map(str, r)) for r in rows[:10]))
                else:
                    parts.append(out.answer)
            except Exception as exc:  # noqa: BLE001 - a failing tool must not abort the plan; it is traced
                res.steps.append(Step(name, question, str(exc), ok=False))
                parts.append(f"[{name} unavailable: {exc}]")
        res.answer = "\n".join(parts)
        return res
