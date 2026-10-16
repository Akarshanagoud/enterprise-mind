"""Offline RAG evaluation: retrieval quality (hit@k, MRR) and safety behaviour (abstain / ACL)."""
from __future__ import annotations

import json
from pathlib import Path

from .config import Principal
from .rag.pipeline import RAGPipeline

ROLES = {r: Principal(r, r) for r in ("viewer", "analyst", "admin")}


def evaluate(rag: RAGPipeline, golden_path: Path, k: int = 4) -> dict:
    """Golden rows: {question, role, expect_source|null, expect_contains|null, expect_abstain}."""
    rows = [json.loads(l) for l in Path(golden_path).read_text(encoding="utf-8").splitlines() if l.strip()]
    hits = rr = 0.0
    answerable = correct_abstain = correct_text = 0
    failures = []
    for row in rows:
        who = ROLES[row.get("role", "viewer")]
        ans = rag.ask(row["question"], who, k=k)
        if row.get("expect_abstain"):
            ok = ans.abstained
            correct_abstain += ok
        else:
            answerable += 1
            sources = [c.source for c in ans.citations]
            exp = row["expect_source"]
            if exp in sources:
                hits += 1
                rr += 1 / (sources.index(exp) + 1)
            ok = (not ans.abstained and exp in sources
                  and row.get("expect_contains", "").lower() in ans.answer.lower())
            correct_text += ok
        if not ok:
            failures.append(row["question"])
    n_abs = len(rows) - answerable
    return {
        "n": len(rows),
        f"hit@{k}": round(hits / answerable, 3) if answerable else None,
        "mrr": round(rr / answerable, 3) if answerable else None,
        "answer_accuracy": round(correct_text / answerable, 3) if answerable else None,
        "abstain_accuracy": round(correct_abstain / n_abs, 3) if n_abs else None,
        "failures": failures,
    }
