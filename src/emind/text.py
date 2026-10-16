"""Shared text utilities."""
from __future__ import annotations

import re

_STOP = frozenset((
    "a", "an", "the", "and", "or", "but", "if", "of", "to", "in", "on", "for", "with", "at", "by", "from",
    "is", "are", "was", "were", "be", "been", "it", "its", "this", "that", "these", "those", "as", "not", "no",
    "do", "does", "did", "what", "which", "who", "whom", "how", "when", "where", "why", "can", "could",
    "should", "would", "will", "shall", "may", "might", "has", "have", "had", "i", "you", "we", "they", "he",
    "she", "them", "our", "your", "their", "me", "my", "about", "into", "than", "then", "there", "here", "so",
    "such", "also", "any", "all",
))
_TOKEN = re.compile(r"[a-z0-9][a-z0-9_.\-]*[a-z0-9]|[a-z0-9]")
_SENT = re.compile(r"(?<=[.!?])\s+|\n+")


_SUFFIXES = (("ations", "at"), ("ation", "at"), ("ings", ""), ("ing", ""), ("ions", ""), ("ion", ""),
             ("ies", "y"), ("ed", ""), ("es", ""), ("s", ""), ("e", ""))


def _stem(t: str) -> str:
    """Tiny suffix stripper so rotation/rotated/rotates, approve/approved/approving and policy/policies
    share a stem. Numbers and identifiers are left alone."""
    if not t.isalpha() or len(t) <= 3 or t.endswith("ss"):
        return t
    for suf, repl in _SUFFIXES:
        if t.endswith(suf) and len(t) - len(suf) >= 3:
            return t[: -len(suf)] + repl
    return t


def tokenize(text: str, keep_stop: bool = False) -> list[str]:
    toks = _TOKEN.findall(text.lower())
    return [_stem(t) for t in toks if keep_stop or t not in _STOP]


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENT.split(text) if s and len(s.strip()) > 2]
