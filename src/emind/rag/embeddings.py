"""Embedders. Default LSA (TF-IDF + SVD) runs offline with zero downloads; SBERT is opt-in and free."""
from __future__ import annotations

import numpy as np
from sklearn.decomposition import TruncatedSVD
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.preprocessing import normalize


class LSAEmbedder:
    """Latent semantic analysis: captures term co-occurrence so related wording still matches."""

    def __init__(self, dims: int = 128):
        self.dims = dims
        self._tfidf: TfidfVectorizer | None = None
        self._svd: TruncatedSVD | None = None

    def fit(self, texts: list[str]) -> None:
        self._tfidf = TfidfVectorizer(ngram_range=(1, 2), sublinear_tf=True, stop_words="english")
        x = self._tfidf.fit_transform(texts)
        n = min(self.dims, x.shape[0] - 1, x.shape[1] - 1)
        self._svd = TruncatedSVD(n_components=n, random_state=7).fit(x) if n >= 2 else None

    def encode(self, texts: list[str]) -> np.ndarray:
        assert self._tfidf is not None, "call fit() first"
        x = self._tfidf.transform(texts)
        dense = self._svd.transform(x) if self._svd is not None else x.toarray()
        return normalize(dense).astype("float32")


class SentenceTransformerEmbedder:
    """Optional: `pip install sentence-transformers` (model is downloaded free from HuggingFace)."""

    def __init__(self, model: str = "sentence-transformers/all-MiniLM-L6-v2"):
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(model)

    def fit(self, texts: list[str]) -> None:  # stateless
        return None

    def encode(self, texts: list[str]) -> np.ndarray:
        return np.asarray(self._model.encode(texts, normalize_embeddings=True), dtype="float32")


def make_embedder(kind: str):
    return SentenceTransformerEmbedder() if kind == "sbert" else LSAEmbedder()


class DenseIndex:
    """Cosine search. Uses FAISS when installed, otherwise exact NumPy matmul."""

    def __init__(self, vectors: np.ndarray):
        self.vectors = vectors
        self._faiss = None
        try:
            import faiss  # type: ignore

            idx = faiss.IndexFlatIP(vectors.shape[1])
            idx.add(vectors)
            self._faiss = idx
        except Exception:  # noqa: BLE001 - FAISS is optional; any failure means exact NumPy search
            self._faiss = None

    def scores(self, query_vec: np.ndarray) -> np.ndarray:
        if self._faiss is not None:
            s, ids = self._faiss.search(query_vec.reshape(1, -1), self.vectors.shape[0])
            out = np.zeros(self.vectors.shape[0], dtype="float32")
            out[ids[0]] = s[0]
            return out
        return self.vectors @ query_vec
