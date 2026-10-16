"""LLM providers. Both are free and keyless: an offline extractive model and local Ollama."""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Protocol

import httpx

from ..models import Passage
from ..text import split_sentences, tokenize

log = logging.getLogger("emind.llm")


class LLM(Protocol):
    name: str

    def answer(self, question: str, passages: list[Passage]) -> str: ...


class ExtractiveLLM:
    """Deterministic, offline, zero-hallucination-by-construction: answers using verbatim evidence.

    Sentences are scored by IDF-weighted coverage of the question, so matching only common words
    ("above", "dollars") is not enough - the rare, meaningful terms must be covered too.
    """

    name = "extractive"

    def __init__(self, weigh: Callable[[str], float] | None = None):
        self.weigh = weigh or (lambda _t: 1.0)

    def answer(self, question: str, passages: list[Passage], max_sentences: int = 3) -> str:
        q = set(tokenize(question))
        total = sum(self.weigh(t) for t in q)
        if total <= 0:
            return ""
        scored = []
        for n, p in enumerate(passages, 1):
            for pos, s in enumerate(split_sentences(p.text)):
                hit = q & set(tokenize(s))
                if hit:
                    scored.append((sum(self.weigh(t) for t in hit) / total, n, pos, s))
        if not scored:
            return ""
        floor = max(0.35, 0.6 * max(x[0] for x in scored))
        top = sorted((x for x in scored if x[0] >= floor), key=lambda x: -x[0])[:max_sentences]
        top.sort(key=lambda x: (x[1], x[2]))
        return " ".join(f"{s} [{n}]" for _, n, _, s in top)


_PROMPT = (
    "You are a careful enterprise assistant. Answer ONLY from the numbered context. "
    "Cite sources like [1]. If the context is insufficient reply exactly: INSUFFICIENT_CONTEXT.\n\n"
    "Context:\n{ctx}\n\nQuestion: {q}\nAnswer:"
)


class OllamaLLM:
    """Local open-weights models via Ollama (https://ollama.com) - free, runs on your machine."""

    name = "ollama"

    def __init__(self, url: str, model: str, timeout: float = 60.0, weigh: Callable[[str], float] | None = None):
        self.url, self.model, self.timeout = url.rstrip("/"), model, timeout
        self._fallback = ExtractiveLLM(weigh)

    def answer(self, question: str, passages: list[Passage]) -> str:
        ctx = "\n".join(f"[{i}] {p.text}" for i, p in enumerate(passages, 1))
        try:
            r = httpx.post(
                f"{self.url}/api/generate",
                json={"model": self.model, "prompt": _PROMPT.format(ctx=ctx, q=question),
                      "stream": False, "options": {"temperature": 0}},
                timeout=self.timeout,
            )
            r.raise_for_status()
            text = r.json().get("response", "").strip()
            return "" if "INSUFFICIENT_CONTEXT" in text else text
        except Exception as exc:  # noqa: BLE001 - never fail a request because the model server is down
            log.warning("ollama unavailable (%s) - falling back to extractive", exc)
            return self._fallback.answer(question, passages)


class OpenAICompatLLM:
    """Any self-hosted OpenAI-compatible server: vLLM, Triton (OpenAI frontend), TGI, llama.cpp, LM Studio.

    Keyless by design - point `url` at your own endpoint, e.g. `vllm serve <model>` -> http://localhost:8000/v1
    """

    name = "openai_compat"

    def __init__(self, url: str, model: str, timeout: float = 60.0, weigh: Callable[[str], float] | None = None):
        self.url, self.model, self.timeout = url.rstrip("/"), model, timeout
        self._fallback = ExtractiveLLM(weigh)

    def answer(self, question: str, passages: list[Passage]) -> str:
        ctx = "\n".join(f"[{i}] {p.text}" for i, p in enumerate(passages, 1))
        try:
            r = httpx.post(
                f"{self.url}/chat/completions", timeout=self.timeout,
                json={"model": self.model, "temperature": 0,
                      "messages": [{"role": "user", "content": _PROMPT.format(ctx=ctx, q=question)}]},
            )
            r.raise_for_status()
            text = r.json()["choices"][0]["message"]["content"].strip()
            return "" if "INSUFFICIENT_CONTEXT" in text else text
        except Exception as exc:  # noqa: BLE001 - never fail a request because the model server is down
            log.warning("openai-compatible server unavailable (%s) - falling back to extractive", exc)
            return self._fallback.answer(question, passages)


def make_llm(provider: str, url: str = "", model: str = "",
             weigh: Callable[[str], float] | None = None) -> LLM:
    """`weigh` is the corpus IDF used by the extractive model (and the fallback of the generative ones)."""
    if provider == "ollama":
        return OllamaLLM(url, model, weigh=weigh)
    if provider == "openai_compat":
        return OpenAICompatLLM(url, model, weigh=weigh)
    return ExtractiveLLM(weigh)
