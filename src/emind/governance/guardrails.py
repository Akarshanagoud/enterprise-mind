"""Input / output guardrails: prompt validation, injection defence, output filtering."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .pii import redact

_INJECTION = [
    r"ignore (?:all |any |the )?(?:previous|prior|above|earlier) (?:instructions|prompts?|rules)",
    r"disregard (?:all |any |the )?(?:previous|prior|above|your) (?:instructions|rules|guidelines)",
    r"(?:reveal|print|show|repeat|leak) (?:me )?(?:your|the) (?:system|hidden|initial) (?:prompt|instructions)",
    r"you are now (?:in )?(?:dan|developer mode|jailbroken)",
    r"\bjailbreak\b",
    r"act as (?:an? )?(?:unrestricted|unfiltered|evil)",
    r"override (?:your )?(?:safety|guardrails|policy|policies)",
]
_INJECTION_RX = re.compile("|".join(_INJECTION), re.IGNORECASE)
_BLOCKED_OUTPUT = re.compile(r"\b(?:how to (?:build|make) a (?:bomb|explosive)|child sexual)\b", re.IGNORECASE)


@dataclass
class GuardResult:
    allowed: bool
    text: str
    reasons: list[str] = field(default_factory=list)
    pii_kinds: list[str] = field(default_factory=list)


def is_injection(text: str) -> bool:
    return bool(_INJECTION_RX.search(text))


def validate_input(prompt: str, max_chars: int = 2000) -> GuardResult:
    reasons: list[str] = []
    if not prompt or not prompt.strip():
        return GuardResult(False, "", ["empty_prompt"])
    if len(prompt) > max_chars:
        return GuardResult(False, "", [f"prompt_too_long>{max_chars}"])
    if any(ord(c) < 32 and c not in "\n\t\r" for c in prompt):
        return GuardResult(False, "", ["control_characters"])
    if is_injection(prompt):
        return GuardResult(False, "", ["prompt_injection"])
    clean, findings = redact(prompt.strip())
    kinds = sorted({f.kind for f in findings})
    if kinds:
        reasons.append("pii_redacted_from_prompt")
    return GuardResult(True, clean, reasons, kinds)


def filter_output(text: str) -> GuardResult:
    if _BLOCKED_OUTPUT.search(text):
        return GuardResult(False, "", ["unsafe_output_blocked"])
    clean, findings = redact(text)
    kinds = sorted({f.kind for f in findings})
    return GuardResult(True, clean, ["pii_redacted_from_output"] if kinds else [], kinds)
