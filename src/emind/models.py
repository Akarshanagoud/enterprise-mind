"""Domain objects shared across the platform."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class Chunk:
    chunk_id: str
    doc_id: str
    text: str
    source: str
    access_level: int = 2
    ordinal: int = 0
    meta: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Passage:
    chunk: Chunk
    score: float
    lexical: float = 0.0
    dense: float = 0.0

    @property
    def text(self) -> str:
        return self.chunk.text
