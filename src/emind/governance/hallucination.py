"""Hallucination monitoring: sentence-level grounding of an answer against its evidence."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..text import split_sentences, tokenize

_CITE = re.compile(r"\[\d+\]")
_NUM = re.compile(r"\d[\d,]*\.?\d*")


@dataclass
class GroundingReport:
    score: float
    unsupported: list[str] = field(default_factory=list)


def _numbers(s: str) -> set[str]:
    return {n.replace(",", "").rstrip(".") for n in _NUM.findall(s)}


def grounding_score(answer: str, contexts: list[str], threshold: float = 0.6) -> GroundingReport:
    """Fraction of answer sentences whose content words (and every number) appear in the evidence."""
    ctx_tokens = set(tokenize(" ".join(contexts)))
    ctx_numbers = _numbers(" ".join(contexts))
    sentences = split_sentences(_CITE.sub("", answer))
    if not sentences:
        return GroundingReport(0.0, [])
    unsupported: list[str] = []
    for s in sentences:
        toks = set(tokenize(s))
        if not toks:
            continue
        overlap = len(toks & ctx_tokens) / len(toks)
        nums_ok = _numbers(s) <= ctx_numbers
        if overlap < threshold or not nums_ok:
            unsupported.append(s)
    supported = len(sentences) - len(unsupported)
    return GroundingReport(round(supported / len(sentences), 3), unsupported)
