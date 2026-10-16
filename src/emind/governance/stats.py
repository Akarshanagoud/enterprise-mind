"""Responsible-AI monitoring: aggregate the audit trail into governance indicators."""
from __future__ import annotations

from collections import Counter
from statistics import mean

BLOCK_FLAGS = {"prompt_injection", "control_characters", "prompt_too_long", "empty_prompt", "unsafe_output_blocked"}
PII_FLAGS = {"pii_redacted_from_prompt", "pii_redacted_from_output"}


def governance_stats(events: list[dict]) -> dict:
    asks = [e for e in events if e["event"] == "ask"]
    answered = [e for e in asks if not e["details"].get("abstained")]
    flags: Counter = Counter()
    for e in events:
        if e["event"] in ("ask", "agent"):
            flags.update(set(e["details"].get("flags", [])))
    grounding = [e["details"]["grounding"] for e in answered if "grounding" in e["details"]]
    latency = sorted(e["details"]["latency_ms"] for e in asks if "latency_ms" in e["details"])
    ratings = Counter(e["details"].get("rating") for e in events if e["event"] == "feedback")
    return {
        "events": len(events),
        "questions": len(asks),
        "answered": len(answered),
        "abstain_rate": round(1 - len(answered) / len(asks), 3) if asks else None,
        "avg_grounding": round(mean(grounding), 3) if grounding else None,
        "blocked": sum(1 for e in asks if BLOCK_FLAGS & set(e["details"].get("flags", []))),
        "pii_redactions": sum(flags[f] for f in PII_FLAGS),
        "poisoned_removed": flags["poisoned_sentence_removed"],
        "access_denied": sum(1 for e in events if e["event"] == "access_denied"),
        "feedback": {"up": ratings["up"], "down": ratings["down"]},
        "latency_ms": {
            "avg": round(mean(latency), 2) if latency else None,
            "p95": latency[min(len(latency) - 1, int(0.95 * len(latency)))] if latency else None,
        },
        "flags": dict(flags.most_common()),
        "by_event": dict(Counter(e["event"] for e in events).most_common()),
    }
