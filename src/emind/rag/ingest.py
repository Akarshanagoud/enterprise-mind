"""Document loading and chunking. Front-matter `access: public|internal|confidential` sets ACLs."""
from __future__ import annotations

import csv
import hashlib
import json
import re
from pathlib import Path

from ..config import ACCESS_LEVELS
from ..models import Chunk
from ..text import split_sentences

_FRONT = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_HEADING = re.compile(r"^#{1,6}[ \t]+(.+?)[ \t]*#*[ \t]*$", re.MULTILINE)


def _parse_front_matter(raw: str) -> tuple[dict, str]:
    m = _FRONT.match(raw)
    if not m:
        return {}, raw
    meta = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip().lower()] = v.strip()
    return meta, raw[m.end():]


def load_file(path: Path) -> tuple[str, dict]:
    suffix = path.suffix.lower()
    if suffix in {".txt", ".md"}:
        meta, body = _parse_front_matter(path.read_text(encoding="utf-8", errors="replace"))
        return body, meta
    if suffix == ".csv":
        with path.open(newline="", encoding="utf-8") as fh:
            rows = [", ".join(f"{k}: {v}" for k, v in r.items()) for r in csv.DictReader(fh)]
        return "\n".join(rows), {}
    if suffix == ".json":
        return json.dumps(json.loads(path.read_text(encoding="utf-8")), indent=1), {}
    if suffix == ".pdf":  # optional dependency: pip install pypdf
        from pypdf import PdfReader

        return "\n".join(p.extract_text() or "" for p in PdfReader(str(path)).pages), {}
    raise ValueError(f"unsupported file type: {suffix}")


def chunk_text(text: str, max_words: int = 90, overlap_sentences: int = 1) -> list[str]:
    """Sentence-aware chunking with sentence overlap so facts are not cut mid-thought."""
    sentences = split_sentences(re.sub(r"^#+\s*", "", text, flags=re.MULTILINE))
    chunks, cur, count = [], [], 0
    for s in sentences:
        w = len(s.split())
        if cur and count + w > max_words:
            chunks.append(" ".join(cur))
            cur = cur[-overlap_sentences:] if overlap_sentences else []
            count = sum(len(x.split()) for x in cur)
        cur.append(s)
        count += w
    if cur:
        chunks.append(" ".join(cur))
    return chunks


def build_chunks(doc_id: str, text: str, source: str, access: str | int = "internal",
                 meta: dict | None = None) -> list[Chunk]:
    level = access if isinstance(access, int) else ACCESS_LEVELS.get(str(access).lower(), 2)
    # headings help retrieval (indexed via meta["title"]) but are not answer sentences
    headings = _HEADING.findall(text)
    body = _HEADING.sub("", text)
    base = {**(meta or {}), "doc_words": len(body.split()), "title": " / ".join(headings) or source}
    return [
        Chunk(f"{doc_id}:{i}", doc_id, c, source, level, i, dict(base))
        for i, c in enumerate(chunk_text(body))
    ]


def load_directory(directory: Path) -> list[Chunk]:
    chunks: list[Chunk] = []
    for path in sorted(Path(directory).rglob("*")):
        if not path.is_file() or path.suffix.lower() not in {".txt", ".md", ".csv", ".json", ".pdf"}:
            continue
        text, meta = load_file(path)
        doc_id = hashlib.sha1(str(path.name).encode()).hexdigest()[:10]
        chunks.extend(build_chunks(doc_id, text, path.name, meta.get("access", "internal"), {"managed": "folder"}))
    return chunks
