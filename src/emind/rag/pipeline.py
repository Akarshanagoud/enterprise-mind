"""End-to-end governed RAG: guard -> retrieve (ACL) -> generate -> filter -> ground -> abstain."""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field, replace

from ..config import Principal, Settings
from ..governance.guardrails import filter_output, is_injection, validate_input
from ..governance.hallucination import grounding_score
from ..models import Passage
from ..text import split_sentences, tokenize
from .llm import LLM
from .retriever import HybridRetriever

ABSTAIN = "I don't have enough reliable information in the documents you can access to answer that."
LEVEL_NAMES = {1: "public", 2: "internal", 3: "confidential"}
_FOLLOW_UP = re.compile(r"\b(it|its|that|this|they|them|their|those|these|what about|how about|same)\b", re.IGNORECASE)


@dataclass
class Citation:
    n: int
    source: str
    chunk_id: str
    score: float
    snippet: str
    access: str = "internal"
    text: str = ""
    cited: bool = False


@dataclass
class RAGAnswer:
    answer: str
    abstained: bool
    citations: list[Citation] = field(default_factory=list)
    grounding: float = 0.0
    flags: list[str] = field(default_factory=list)
    latency_ms: float = 0.0
    query: str = ""


def condense(question: str, history: list[str] | None) -> str:
    """Make follow-ups ("what about digital products?") retrievable by folding in the previous turn."""
    prev = next((h for h in reversed(history or []) if h.strip()), "")
    if prev and (len(tokenize(question)) <= 3 or _FOLLOW_UP.search(question)):
        return f"{prev[:500]} {question}"
    return question


class RAGPipeline:
    def __init__(self, retriever: HybridRetriever, llm: LLM, settings: Settings):
        self.retriever, self.llm, self.settings = retriever, llm, settings

    def ask(self, question: str, principal: Principal, k: int = 4,
            history: list[str] | None = None) -> RAGAnswer:
        t0 = time.perf_counter()

        def done(a: RAGAnswer) -> RAGAnswer:
            a.flags = list(dict.fromkeys(a.flags))
            a.latency_ms = round((time.perf_counter() - t0) * 1000, 2)
            return a

        guard = validate_input(question, self.settings.max_input_chars)
        if not guard.allowed:
            return done(RAGAnswer("Request blocked by input policy.", True, flags=guard.reasons))
        flags = list(guard.reasons)
        # earlier turns are untrusted too: each one passes the same input policy or is ignored
        prior = [validate_input(h, self.settings.max_input_chars).text for h in (history or [])[-3:]]
        query = condense(guard.text, prior)

        passages = self.retriever.search(query, k=k, clearance=principal.clearance)
        safe = []  # indirect-injection defence: strip poisoned sentences, keep the legitimate facts
        for p in passages:
            sents = split_sentences(p.text)
            kept = [s for s in sents if not is_injection(s)]
            if len(kept) != len(sents):
                flags.append("poisoned_sentence_removed")
                if not kept:
                    continue
                p = Passage(replace(p.chunk, text=" ".join(kept)), p.score, p.lexical, p.dense)
            safe.append(p)
        if not safe:
            return done(RAGAnswer(ABSTAIN, True, flags=flags + ["no_evidence"], query=query))

        draft = self.llm.answer(query, safe)
        if not draft:
            return done(RAGAnswer(ABSTAIN, True, flags=flags + ["llm_insufficient_context"], query=query))

        out = filter_output(draft)
        if not out.allowed:
            return done(RAGAnswer(ABSTAIN, True, flags=flags + out.reasons, query=query))
        flags += out.reasons

        report = grounding_score(out.text, [p.text for p in safe])
        if report.score < self.settings.min_grounding:
            return done(RAGAnswer(ABSTAIN, True, grounding=report.score,
                                  flags=flags + ["low_grounding_abstained"], query=query))

        cites = [Citation(i, p.chunk.source, p.chunk.chunk_id, round(p.score, 4), p.text[:160],
                          LEVEL_NAMES.get(p.chunk.access_level, "internal"), p.text, f"[{i}]" in out.text)
                 for i, p in enumerate(safe, 1)]
        return done(RAGAnswer(out.text, False, cites, report.score, flags, query=query))
