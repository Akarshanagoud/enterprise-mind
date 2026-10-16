"""Hybrid retrieval: BM25 + dense vectors fused with Reciprocal Rank Fusion, ACL-filtered."""
from __future__ import annotations

import json
import math
import threading
from collections import Counter
from pathlib import Path

import numpy as np

from ..models import Chunk, Passage
from ..text import tokenize
from .embeddings import DenseIndex, make_embedder


class BM25:
    def __init__(self, docs: list[list[str]], k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        self.tf = [Counter(d) for d in docs]
        self.dl = np.array([len(d) for d in docs], dtype="float32")
        self.avg = float(self.dl.mean()) if len(docs) else 0.0
        df: Counter = Counter()
        for t in self.tf:
            df.update(t.keys())
        n = len(docs)
        self.idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}

    def scores(self, query: list[str]) -> np.ndarray:
        out = np.zeros(len(self.tf), dtype="float32")
        for i, tf in enumerate(self.tf):
            norm = self.k1 * (1 - self.b + self.b * self.dl[i] / (self.avg or 1))
            for t in query:
                f = tf.get(t)
                if f:
                    out[i] += self.idf[t] * f * (self.k1 + 1) / (f + norm)
        return out


class HybridRetriever:
    def __init__(self, embedder_kind: str = "lsa", min_dense: float = 0.2):
        self.embedder_kind = embedder_kind
        self.min_dense = min_dense
        self.chunks: list[Chunk] = []
        self._lock = threading.RLock()
        self._bm25: BM25 | None = None
        self._dense: DenseIndex | None = None
        self._embedder = None

    # ---- indexing -------------------------------------------------------------------------
    def add(self, chunks: list[Chunk]) -> int:
        with self._lock:
            known = {c.chunk_id for c in self.chunks}
            new = [c for c in chunks if c.chunk_id not in known]
            self.chunks.extend(new)
            self._rebuild()
            return len(new)

    def upsert(self, chunks: list[Chunk]) -> int:
        """Replace every document present in `chunks` - re-ingesting a document never leaves stale chunks."""
        with self._lock:
            ids = {c.doc_id for c in chunks}
            self.chunks = [c for c in self.chunks if c.doc_id not in ids] + list(chunks)
            self._rebuild()
            return len(chunks)

    def sync_managed(self, chunks: list[Chunk], tag: str = "folder") -> int:
        """Mirror a source of truth (the docs folder): drop everything it previously owned, add its current state."""
        with self._lock:
            ids = {c.doc_id for c in chunks}
            self.chunks = [c for c in self.chunks
                           if c.meta.get("managed") != tag and c.doc_id not in ids] + list(chunks)
            self._rebuild()
            return len(chunks)

    def remove(self, doc_id: str) -> int:
        with self._lock:
            before = len(self.chunks)
            self.chunks = [c for c in self.chunks if c.doc_id != doc_id]
            removed = before - len(self.chunks)
            if removed:
                self._rebuild()
            return removed

    def documents(self, clearance: int = 3) -> list[dict]:
        docs: dict[str, dict] = {}
        for c in self.chunks:
            if c.access_level > clearance:
                continue
            d = docs.setdefault(c.doc_id, {
                "doc_id": c.doc_id, "source": c.source, "title": c.meta.get("title", c.source),
                "access_level": c.access_level, "chunks": 0,
                "words": c.meta.get("doc_words", 0), "managed": c.meta.get("managed", "api"),
                "uploaded_by": c.meta.get("uploaded_by"), "ingested_at": c.meta.get("ingested_at"),
            })
            d["chunks"] += 1
        return sorted(docs.values(), key=lambda d: d["source"].lower())

    def idf(self, token: str) -> float:
        """Corpus rarity of a (stemmed) token. Words no document contains get the median weight: they may be
        mere phrasing ("how long") or the very concept the corpus lacks ("visa"), so they neither vanish
        nor dominate - an answer must still cover the question's rare, meaningful terms."""
        bm = self._bm25
        if bm is None:
            return 1.0
        return bm.idf.get(token, self._unseen_idf)

    @property
    def vector_backend(self) -> str:
        return "faiss" if self._dense is not None and self._dense._faiss is not None else "numpy"

    def _rebuild(self) -> None:
        if not self.chunks:
            self._bm25 = self._dense = self._embedder = None
            return
        texts = [f"{c.meta.get('title', '')}\n{c.text}" for c in self.chunks]  # title boosts recall
        self._bm25 = BM25([tokenize(t) for t in texts])
        self._unseen_idf = float(np.median(list(self._bm25.idf.values()))) if self._bm25.idf else 1.0
        emb = make_embedder(self.embedder_kind)
        emb.fit(texts)
        self._embedder, self._dense = emb, DenseIndex(emb.encode(texts))

    def __len__(self) -> int:
        return len(self.chunks)

    # ---- persistence (JSONL only - never unpickle untrusted state) --------------------------
    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as fh:
            for c in self.chunks:
                fh.write(json.dumps(c.to_dict()) + "\n")

    def load(self, path: Path) -> int:
        if not path.exists():
            return 0
        with path.open(encoding="utf-8") as fh:
            chunks = [Chunk(**json.loads(l)) for l in fh if l.strip()]
        return self.add(chunks)

    # ---- search ---------------------------------------------------------------------------
    def search(self, query: str, k: int = 4, clearance: int = 1) -> list[Passage]:
        with self._lock:
            if not self.chunks or self._bm25 is None or self._dense is None:
                return []
            lex = self._bm25.scores(tokenize(query))
            den = self._dense.scores(self._embedder.encode([query])[0])
            allowed = np.array([c.access_level <= clearance for c in self.chunks])
            ranks: dict[int, float] = {}
            for arr in (lex, den):
                masked = np.where(allowed, arr, -np.inf)
                for rank, idx in enumerate(np.argsort(-masked)[:50]):
                    if not np.isfinite(masked[idx]) or masked[idx] <= 0:
                        continue
                    ranks[idx] = ranks.get(idx, 0.0) + 1.0 / (60 + rank + 1)
            out = []
            for idx, score in sorted(ranks.items(), key=lambda kv: -kv[1]):
                if lex[idx] <= 0 and den[idx] < self.min_dense:
                    continue  # no lexical match and weak semantic match -> irrelevant
                out.append(Passage(self.chunks[idx], score, float(lex[idx]), float(den[idx])))
                if len(out) == k:
                    break
            return out
