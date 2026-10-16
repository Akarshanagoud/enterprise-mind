"""PII detection and redaction (regex + Luhn validation, no external services)."""
from __future__ import annotations

import re
from dataclasses import dataclass

_PATTERNS = {
    "EMAIL": re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    "SSN": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "CREDIT_CARD": re.compile(r"\b(?:\d[ -]?){13,19}\b"),
    "PHONE": re.compile(r"(?<!\d)(?:\+?\d{1,2}[ .-])?\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}(?!\d)"),
    "IPV4": re.compile(r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b"),
}


@dataclass(frozen=True)
class PIIFinding:
    kind: str
    start: int
    end: int


def _luhn(number: str) -> bool:
    digits = [int(c) for c in re.sub(r"\D", "", number)]
    if not 13 <= len(digits) <= 19:
        return False
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2:
            d *= 2
            d -= 9 if d > 9 else 0
        total += d
    return total % 10 == 0


def find_pii(text: str) -> list[PIIFinding]:
    found: list[PIIFinding] = []
    for kind, rx in _PATTERNS.items():
        for m in rx.finditer(text):
            if kind == "CREDIT_CARD" and not _luhn(m.group()):
                continue
            found.append(PIIFinding(kind, m.start(), m.end()))
    # drop overlaps, keep earliest/longest
    found.sort(key=lambda f: (f.start, -(f.end - f.start)))
    out: list[PIIFinding] = []
    for f in found:
        if not out or f.start >= out[-1].end:
            out.append(f)
    return out


def redact(text: str) -> tuple[str, list[PIIFinding]]:
    findings = find_pii(text)
    for f in reversed(findings):
        text = text[: f.start] + f"[{f.kind}]" + text[f.end :]
    return text, findings
