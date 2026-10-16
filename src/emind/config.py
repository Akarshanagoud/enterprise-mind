"""Central configuration. Everything is driven by environment variables (12-factor)."""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("emind.config")

ROLE_CLEARANCE = {"viewer": 1, "analyst": 2, "admin": 3}
ACCESS_LEVELS = {"public": 1, "internal": 2, "confidential": 3}


@dataclass(frozen=True)
class Principal:
    user: str
    role: str

    @property
    def clearance(self) -> int:
        return ROLE_CLEARANCE.get(self.role, 0)

    def can(self, *roles: str) -> bool:
        return self.role in roles


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    docs_dir: Path
    llm_provider: str          # "extractive" (offline, default) | "ollama" (local, free)
    ollama_url: str
    ollama_model: str
    embedder: str              # "lsa" (default, no download) | "sbert" (sentence-transformers)
    api_keys: dict             # key -> Principal
    rate_limit_per_min: int
    min_grounding: float       # below this the system abstains instead of answering
    max_input_chars: int
    golden_path: Path = Path("./data/golden.jsonl")
    demo_mode: bool = False    # True when running on the built-in development keys

    @property
    def warehouse_path(self) -> Path:
        return self.data_dir / "warehouse.db"

    @property
    def index_path(self) -> Path:
        return self.data_dir / "chunks.jsonl"

    @property
    def audit_path(self) -> Path:
        return self.data_dir / "audit.jsonl"

    @classmethod
    def from_env(cls, **overrides) -> Settings:
        data_dir = Path(os.getenv("EMIND_DATA_DIR", "./var"))
        raw = os.getenv("EMIND_API_KEYS", "")
        keys: dict = {}
        for item in filter(None, (s.strip() for s in raw.split(","))):
            key, role, user = (item.split(":") + ["", ""])[:3]
            if key and role in ROLE_CLEARANCE:
                keys[key] = Principal(user or role, role)
        demo = not keys
        if demo:
            log.warning("EMIND_API_KEYS not set - using INSECURE development keys")
            keys = {
                "dev-admin-key": Principal("dev-admin", "admin"),
                "dev-analyst-key": Principal("dev-analyst", "analyst"),
                "dev-viewer-key": Principal("dev-viewer", "viewer"),
            }
        values = {
            "data_dir": data_dir,
            "docs_dir": Path(os.getenv("EMIND_DOCS_DIR", "./data/sample_docs")),
            "llm_provider": os.getenv("EMIND_LLM", "extractive"),
            "ollama_url": os.getenv("OLLAMA_URL", "http://localhost:11434"),
            "ollama_model": os.getenv("OLLAMA_MODEL", "llama3.2"),
            "embedder": os.getenv("EMIND_EMBEDDER", "lsa"),
            "api_keys": keys,
            "rate_limit_per_min": int(os.getenv("EMIND_RATE_LIMIT", "120")),
            "min_grounding": float(os.getenv("EMIND_MIN_GROUNDING", "0.6")),
            "max_input_chars": int(os.getenv("EMIND_MAX_INPUT", "2000")),
            "golden_path": Path(os.getenv("EMIND_GOLDEN", "./data/golden.jsonl")),
            "demo_mode": demo,
        }
        values.update(overrides)
        return cls(**values)
