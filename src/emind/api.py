"""FastAPI service: API-key auth, RBAC, rate limiting, audit trail, Prometheus metrics, web console."""
from __future__ import annotations

import tempfile
import threading
import time
import uuid
from collections import defaultdict, deque
from dataclasses import asdict
from pathlib import Path
from typing import Annotated, Literal

from fastapi import Body, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__, analytics
from .agent import Agent
from .config import ACCESS_LEVELS, Principal, Settings
from .etl.pipeline import generate_sample_csv, run_etl
from .evaluation import evaluate
from .extraction import extract_invoice
from .governance.audit import AuditLog
from .governance.guardrails import is_injection, validate_input
from .governance.pii import redact
from .governance.stats import governance_stats
from .rag.ingest import build_chunks, load_directory
from .rag.llm import make_llm
from .rag.pipeline import LEVEL_NAMES, RAGPipeline
from .rag.retriever import HybridRetriever
from .streaming import StreamProcessor

STATIC_DIR = Path(__file__).parent / "static"
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
       "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    top_k: int = Field(4, ge=1, le=10)
    history: list[Annotated[str, Field(max_length=4000)]] = Field(default_factory=list, max_length=6)


class IngestRequest(BaseModel):
    doc_id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9._-]+$")
    text: str = Field(min_length=1, max_length=200_000)
    source: str = Field("api", max_length=200)
    access: str = Field("internal", pattern="^(public|internal|confidential)$")


class ExtractRequest(BaseModel):
    text: str = Field(min_length=1, max_length=20_000)


class FeedbackRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    answer: str = Field("", max_length=8000)
    rating: Literal["up", "down"]
    comment: str = Field("", max_length=1000)


class Metrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.counts: dict = defaultdict(int)
        self.latency_sum: dict = defaultdict(float)

    def observe(self, route: str, status: int, seconds: float) -> None:
        with self._lock:
            self.counts[(route, status)] += 1
            self.latency_sum[route] += seconds

    def render(self) -> str:
        lines = ["# TYPE emind_requests_total counter"]
        for (route, status), n in sorted(self.counts.items()):
            lines.append(f'emind_requests_total{{route="{route}",status="{status}"}} {n}')
        lines.append("# TYPE emind_request_seconds_sum counter")
        for route, s in sorted(self.latency_sum.items()):
            lines.append(f'emind_request_seconds_sum{{route="{route}"}} {s:.4f}')
        return "\n".join(lines) + "\n"


class RateLimiter:
    def __init__(self, per_minute: int) -> None:
        self.per_minute = per_minute
        self._hits: dict = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] > 60:
                q.popleft()
            if len(q) >= self.per_minute:
                return False
            q.append(now)
            return True


def create_app(settings: Settings | None = None, auto_ingest: bool = True) -> FastAPI:
    cfg = settings or Settings.from_env()
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    retriever = HybridRetriever(cfg.embedder)
    retriever.load(cfg.index_path)
    if auto_ingest and cfg.docs_dir.exists():  # the docs folder is a source of truth: mirror it on start
        retriever.sync_managed(load_directory(cfg.docs_dir))
        retriever.save(cfg.index_path)
    llm = make_llm(cfg.llm_provider, cfg.ollama_url, cfg.ollama_model, weigh=retriever.idf)
    rag = RAGPipeline(retriever, llm, cfg)
    agent = Agent(rag, cfg.warehouse_path)
    audit, metrics, limiter = AuditLog(cfg.audit_path), Metrics(), RateLimiter(cfg.rate_limit_per_min)

    app = FastAPI(title="EnterpriseMind", version=__version__)
    app.state.retriever, app.state.audit, app.state.cfg, app.state.rag = retriever, audit, cfg, rag

    @app.middleware("http")
    async def observe(request: Request, call_next):
        rid = request.headers.get("x-request-id", uuid.uuid4().hex)
        t0 = time.perf_counter()
        response = await call_next(request)
        path = request.url.path
        metrics.observe(path, response.status_code, time.perf_counter() - t0)
        response.headers["x-request-id"] = rid
        response.headers["x-content-type-options"] = "nosniff"
        response.headers["referrer-policy"] = "no-referrer"
        if not path.startswith(("/docs", "/redoc", "/openapi")):  # Swagger UI loads its own CDN assets
            response.headers["content-security-policy"] = CSP
        if path == "/" or path.startswith("/static/"):
            response.headers["cache-control"] = "no-cache"
        return response

    def auth(x_api_key: str = Header(default="")) -> Principal:
        principal = cfg.api_keys.get(x_api_key)
        if principal is None:
            raise HTTPException(401, "invalid or missing X-API-Key")
        if not limiter.allow(x_api_key):
            raise HTTPException(429, "rate limit exceeded")
        return principal

    def require(*roles: str):
        def dep(p: Principal = Depends(auth)) -> Principal:
            if not p.can(*roles):
                audit.record("access_denied", p.user, required=list(roles))
                raise HTTPException(403, f"requires role: {', '.join(roles)}")
            return p
        return dep

    admin_only, analyst_or_admin = require("admin"), require("analyst", "admin")

    def find_doc(doc_id: str) -> dict | None:
        return next((d for d in retriever.documents() if d["doc_id"] == doc_id), None)

    # ---- console & probes ------------------------------------------------------------------
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    def console():
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/health")
    def health():
        return {"status": "ok", "version": __version__, "demo_mode": cfg.demo_mode}

    @app.get("/ready")
    def ready():
        return {"ready": True, "chunks": len(retriever), "llm": cfg.llm_provider, "embedder": cfg.embedder}

    @app.get("/metrics", response_class=PlainTextResponse)
    def prom(_: Principal = Depends(admin_only)):
        return metrics.render()

    @app.get("/me")
    def me(p: Principal = Depends(auth)):
        return {"user": p.user, "role": p.role, "clearance": p.clearance,
                "can_read": [name for name, lvl in ACCESS_LEVELS.items() if lvl <= p.clearance]}

    @app.get("/system")
    def system(_: Principal = Depends(auth)):
        return {"version": __version__, "llm": cfg.llm_provider,
                "llm_model": None if cfg.llm_provider == "extractive" else cfg.ollama_model,
                "embedder": cfg.embedder, "vector_backend": retriever.vector_backend,
                "documents": len(retriever.documents()), "chunks": len(retriever),
                "rate_limit_per_min": cfg.rate_limit_per_min, "min_grounding": cfg.min_grounding,
                "max_input_chars": cfg.max_input_chars, "demo_mode": cfg.demo_mode}

    # ---- assistant -------------------------------------------------------------------------
    @app.post("/ask")
    def ask(body: AskRequest, p: Principal = Depends(auth)):
        result = rag.ask(body.question, p, k=body.top_k, history=body.history)
        audit.record("ask", p.user, question=redact(body.question)[0][:200], abstained=result.abstained,
                     grounding=result.grounding, flags=result.flags, latency_ms=result.latency_ms)
        return asdict(result)

    @app.post("/agent")
    def run_agent(body: AskRequest, p: Principal = Depends(auth)):
        result = agent.run(body.question, p)
        flags = [f for s in result.steps if s.ok and not isinstance(s.output, (str, dict)) for f in s.output.flags]
        audit.record("agent", p.user, route=result.route, question=redact(body.question)[0][:200],
                     ok=all(s.ok for s in result.steps), flags=flags)
        return {"answer": result.answer, "route": result.route,
                "steps": [{"tool": s.tool, "ok": s.ok,
                           "output": s.output if isinstance(s.output, (str, dict)) else asdict(s.output)}
                          for s in result.steps]}

    @app.post("/feedback")
    def feedback(body: FeedbackRequest, p: Principal = Depends(auth)):
        audit.record("feedback", p.user, rating=body.rating, question=redact(body.question)[0][:200],
                     comment=redact(body.comment)[0][:500])
        return {"recorded": True}

    @app.post("/extract/invoice")
    def extract(body: ExtractRequest, p: Principal = Depends(auth)):
        try:
            record, method = extract_invoice(
                body.text, cfg.ollama_url if cfg.llm_provider == "ollama" else None, cfg.ollama_model)
        except ValueError as exc:
            raise HTTPException(422, str(exc))
        audit.record("extract_invoice", p.user, method=method)
        return {"method": method, "record": record.model_dump(mode="json")}

    # ---- knowledge base --------------------------------------------------------------------
    @app.get("/documents")
    def documents(p: Principal = Depends(auth)):
        docs = retriever.documents(p.clearance)
        for d in docs:
            d["access"] = LEVEL_NAMES[d.pop("access_level")]
        return {"documents": docs, "chunks": sum(d["chunks"] for d in docs)}

    @app.post("/ingest")
    def ingest(body: IngestRequest, p: Principal = Depends(analyst_or_admin)):
        if ACCESS_LEVELS[body.access] > p.clearance:
            audit.record("access_denied", p.user, action="ingest", access=body.access)
            raise HTTPException(403, f"you cannot classify a document above your own clearance ({body.access})")
        existing = find_doc(body.doc_id)
        if existing and existing["managed"] == "folder":
            raise HTTPException(409, "a document synced from the documents folder already uses this id")
        if existing and existing["access_level"] > p.clearance:
            raise HTTPException(403, "a document you are not cleared to read already uses this id")
        chunks = build_chunks(body.doc_id, body.text, body.source, body.access,
                              {"managed": "api", "uploaded_by": p.user, "ingested_at": round(time.time(), 3)})
        retriever.upsert(chunks)
        retriever.save(cfg.index_path)
        audit.record("ingest", p.user, doc_id=body.doc_id, source=body.source, chunks=len(chunks),
                     access=body.access, replaced=bool(existing))
        return {"doc_id": body.doc_id, "chunks": len(chunks), "chunks_added": len(chunks),
                "replaced": bool(existing), "total_chunks": len(retriever)}

    @app.delete("/documents/{doc_id}")
    def delete_document(doc_id: str, p: Principal = Depends(admin_only)):
        doc = find_doc(doc_id)
        if doc is None:
            raise HTTPException(404, "document not found")
        if doc["managed"] == "folder":
            raise HTTPException(409, "this document is synced from the documents folder; remove the file there")
        removed = retriever.remove(doc_id)
        retriever.save(cfg.index_path)
        audit.record("document_deleted", p.user, doc_id=doc_id, source=doc["source"], chunks=removed)
        return {"doc_id": doc_id, "chunks_removed": removed}

    @app.post("/search")
    def search(body: AskRequest, p: Principal = Depends(analyst_or_admin)):
        """Retrieval inspector: ranked passages with their lexical and semantic scores (ACL-filtered)."""
        guard = validate_input(body.question, cfg.max_input_chars)
        if not guard.allowed:
            raise HTTPException(422, f"blocked by input policy: {', '.join(guard.reasons)}")
        hits = retriever.search(guard.text, k=body.top_k, clearance=p.clearance)
        return {"query": guard.text, "results": [
            {"rank": i, "source": hit.chunk.source, "chunk_id": hit.chunk.chunk_id,
             "access": LEVEL_NAMES[hit.chunk.access_level], "score": round(hit.score, 4),
             "lexical": round(hit.lexical, 3), "dense": round(hit.dense, 3), "text": hit.text,
             "suspicious": is_injection(hit.text)}
            for i, hit in enumerate(hits, 1)]}

    # ---- data platform ---------------------------------------------------------------------
    @app.get("/analytics/summary")
    def analytics_summary(period: str = "12m", region: str | None = None,
                          _: Principal = Depends(analyst_or_admin)):
        try:
            return analytics.summary(cfg.warehouse_path, period, region or None)
        except ValueError as exc:
            raise HTTPException(422, str(exc))

    @app.get("/etl/runs")
    def etl_runs(n: int = Query(20, ge=1, le=200), _: Principal = Depends(analyst_or_admin)):
        return analytics.etl_runs(cfg.warehouse_path, n)

    @app.get("/etl/quarantine")
    def etl_quarantine(n: int = Query(25, ge=1, le=200), _: Principal = Depends(analyst_or_admin)):
        return analytics.quarantine(cfg.warehouse_path, n)

    @app.post("/etl/run")
    async def etl(request: Request, p: Principal = Depends(admin_only)):
        """POST a raw CSV body (Content-Type: text/csv), or an empty body to run the sample dataset."""
        body = await request.body()
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / ("upload.csv" if body else "sample_orders.csv")
            if body:
                src.write_bytes(body)
            else:
                generate_sample_csv(src)
            try:
                report = run_etl(src, cfg.warehouse_path)
            except ValueError as exc:
                raise HTTPException(422, str(exc))
        audit.record("etl_run", p.user, **report.to_dict())
        return report.to_dict()

    @app.post("/stream/events")
    def stream_events(events: list[dict] = Body(..., max_length=5000),
                      p: Principal = Depends(analyst_or_admin)):
        """Micro-batch ingest of order events (same validation, quarantine and CDC as batch ETL)."""
        totals = StreamProcessor(cfg.warehouse_path).process(events)
        audit.record("stream_ingest", p.user, **totals)
        return totals

    # ---- governance ------------------------------------------------------------------------
    @app.get("/governance/stats")
    def governance(_: Principal = Depends(admin_only)):
        return {"chain_valid": audit.verify(), **governance_stats(audit.tail(None))}

    @app.post("/eval/run")
    def run_eval(p: Principal = Depends(admin_only)):
        if not cfg.golden_path.exists():
            raise HTTPException(404, f"golden set not found: {cfg.golden_path}")
        res = evaluate(rag, cfg.golden_path)
        audit.record("eval_run", p.user, **{k: v for k, v in res.items() if k != "failures"},
                     failures=len(res["failures"]))
        return {**res, "ran_at": time.time()}

    @app.get("/audit")
    def audit_tail(n: int = 50, _: Principal = Depends(admin_only)):
        return {"chain_valid": audit.verify(), "events": audit.tail(min(max(n, 1), 500))}

    return app
