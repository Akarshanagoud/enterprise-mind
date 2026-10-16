# EnterpriseMind

A governed, **local-first** GenAI + data-engineering platform. Zero API keys, zero paid services:
everything runs offline by default, and free local models (Ollama / HuggingFace) are opt-in.

```
              ┌────────────── FastAPI (auth · RBAC · rate-limit · metrics · audit) ──────────────┐
 question ──► │ input guard ─► ACL-filtered hybrid retrieval ─► LLM ─► output filter ─► grounding │ ─► answer + citations
              │  (PII, injection)  (BM25 + vectors, RRF)    (offline│Ollama)  (PII)   (abstain)   │      or safe abstention
              ├──────────────────────────────────────────────────────────────────────────────────┤
              │ Agent: plan ─► tools {read-only SQL, doc search} ─► trace                          │
              │ Extraction: text ─► Pydantic-validated invoice (LLM+retry, rules fallback)         │
              │ ETL: CSV ─► validate/quarantine ─► transform ─► CDC upsert (SQLite │ Spark │ Scala)│
              └──────────────────────────────────────────────────────────────────────────────────┘
```

## Quick start
```bash
pip install -e ".[dev]"
python -m emind serve          # open http://127.0.0.1:8000 and pick a demo account
python -m pytest -q            # 30 tests
python -m emind eval           # golden-set regression eval (17 questions: hit@k, MRR, accuracy, refusals)
python -m emind stream events.jsonl   # real-time micro-batch ingest (or kafka://host:9092/topic)
python -m emind demo           # ETL + CDC + governed RAG + agent, in the terminal
EMIND_API_KEYS="k1:admin:alice,k2:viewer:bob" python -m emind serve   # production keys (demo accounts disabled)
```

The documents in `data/sample_docs/` (including the ones labelled confidential) and the generated sales orders are fictional sample data.

## Web console
A complete operator console ships with the API at `/` - plain HTML/CSS/JS with no build step, no CDN and no
third-party code, served under a strict Content-Security-Policy so it works fully offline.

| Page | Who | What it does |
|---|---|---|
| **Assistant** | everyone | Multi-turn chat with clickable citations, an evidence panel, grounding/abstention badges, guardrail notices, copy and 👍/👎 feedback. *Agent* mode shows the plan, the generated SQL and a chart/table of the result. |
| **Knowledge base** | everyone (uploads: analyst+) | Library with classification badges; drag-and-drop upload (classified up to your own clearance); a **retrieval inspector** showing BM25, semantic and fused scores and flagging poisoned passages. |
| **Analytics** | analyst+ | KPI tiles with period-over-period deltas, monthly revenue trend, region/product breakdowns, period and region filters, pipeline runs, data-quality quarantine, **live stream simulation** and batch ETL (admin). |
| **Governance** | admin | Audit-chain integrity, responsible-AI indicators (abstention, grounding, attacks blocked, poisoned sources, PII, access denials, user rating), guardrail charts, one-click **golden-set evaluation**, searchable audit log. |
| **System** | everyone | Runtime configuration, the role/permission matrix, and copy-ready cURL / Python / Java snippets. |

Every chart is single-series in a colour-blind-validated palette with hover/keyboard tooltips and a table view,
in light and dark mode. The layout adapts to tablet (icon rail) and phone (bottom tab bar).
```bash
curl -H "x-api-key: k2" -H "content-type: application/json" \
     -d '{"question":"How many days do customers have to request a refund?"}' localhost:8000/ask
```

## Enterprise features
| Concern | Implementation |
|---|---|
| AuthN / AuthZ | API keys → roles (`viewer < analyst < admin`); role gates endpoints **and** document ACLs |
| Document security | Per-document `access: public/internal/confidential` front-matter; filtered *before* ranking |
| Responsible AI | PII redaction (Luhn-validated), prompt-injection defence incl. **indirect** injection in documents, output filtering, sentence-level hallucination grounding, abstention |
| Auditability | Hash-chained, tamper-evident audit log (`GET /audit` verifies the chain) |
| Observability | Prometheus `/metrics`, request IDs, `/health`, `/ready` |
| Data quality | Validation rules, quarantine table, run ledger, idempotent CDC via row hashes |
| Agent safety | SQL is SELECT-only, single-statement, table-allowlisted, read-only connection, row-capped |
| Deployment | Non-root Dockerfile, compose, Kubernetes (HPA, probes, secrets), GitHub Actions CI |
| Resilience | LLM outage degrades to extractive answers instead of failing |
| Answer quality | IDF-weighted evidence selection: an answer must cover the question's rare terms, so matching only common words ("above", "dollars") abstains instead of answering off-topic |
| Conversation | Follow-ups ("what about digital products?") are condensed with the previous turn; earlier turns pass the same input policy |
| Document lifecycle | Re-ingest replaces a document (no stale chunks); the docs folder is mirrored on start; uploads can't exceed the uploader's clearance; deletes are admin-only and audited |
| Web security | Strict CSP, `nosniff`, `no-referrer`; the console inserts untrusted text only via `textContent` |

## API
| Endpoint | Role | Purpose |
|---|---|---|
| `POST /ask` | any | Governed RAG answer (`history` enables follow-ups) |
| `POST /agent` | any (SQL tool: analyst+) | Plan → tools → trace |
| `POST /feedback` | any | 👍/👎 on an answer, recorded in the audit log |
| `GET /documents` · `POST /ingest` · `DELETE /documents/{id}` | any · analyst+ · admin | Library, upload/replace, delete |
| `POST /search` | analyst+ | Retrieval inspector (scores per passage) |
| `GET /analytics/summary` · `/etl/runs` · `/etl/quarantine` | analyst+ | Dashboard data |
| `POST /stream/events` · `POST /etl/run` | analyst+ · admin | Real-time and batch ingestion |
| `GET /governance/stats` · `POST /eval/run` · `GET /audit` · `GET /metrics` | admin | Governance, evaluation, audit, Prometheus |
| `GET /me` · `/system` · `/health` · `/ready` | any · any · public · public | Session, configuration, probes |

## Skill map
- **Python / FastAPI / Pydantic / Microservices** – `api.py`, `extraction.py`
- **RAG, embeddings, semantic search, vector search** – `rag/` (LSA default, FAISS + SBERT optional)
- **Web console** – served at `/` (`static/`), no build step
- **Streaming / real-time / Kafka** – `streaming.py`, `POST /stream/events`
- **Orchestration** – `dags/emind_daily.py` (Airflow: ETL → quality gate → reindex → eval gate)
- **Evaluation** – `evaluation.py` + `data/golden.jsonl`
- **Hive / Hadoop / Java** – `hive/tables.hql`, `java/EmindClient.java`
- **Agents / agentic workflows** – `agent.py`
- **Structured output extraction** – `extraction.py`
- **LoRA / QLoRA / PEFT / instruction tuning** – `scripts/finetune_lora.py`
- **LLM serving** – `EMIND_LLM=ollama` or `EMIND_LLM=openai_compat` (vLLM / Triton / TGI / llama.cpp; set `OLLAMA_URL` to the `/v1` base and `OLLAMA_MODEL`)
- **ETL, CDC, batch, Spark, PySpark, Scala** – `etl/pipeline.py`, `etl/spark_job.py`, `scala/SalesEtl.scala`
- **SQL / T-SQL / warehousing** – `sql/reporting.sql`, SQLite warehouse
- **Docker / Kubernetes / CI-CD** – `Dockerfile`, `deploy/k8s`, `.github/workflows`
- **Responsible AI** – `governance/`

## Team
| Maintainer | Focus |
|---|---|
| Akarsha Namachanpally ([@Akarshanagoud](https://github.com/Akarshanagoud)) | AI & data engineering: RAG, agent, extraction, ETL and streaming, Spark/Hive/Airflow, fine-tuning |
| Virinchi Sai ([@virinchisai](https://github.com/virinchisai)) | Security, governance & quality: guardrails, audit, API security, evaluation, tests, web console, CI/CD and deployment |

Ownership, the review agreement and each maintainer's backlog are in [CONTRIBUTORS.md](CONTRIBUTORS.md).

## Honest limits and roadmap
- Default embeddings (LSA) are lightweight; install `sentence-transformers` + `faiss-cpu` and set `EMIND_EMBEDDER=sbert` for stronger semantic recall.
- The index is rebuilt on ingest (fine up to ~100k chunks); swap in an incremental ANN store (Chroma / pgvector) beyond that.
- API keys are static; for SSO put the service behind an OIDC proxy or replace `auth()` with JWT validation.
- Rate limiting and metrics are per-process; use a shared store (Redis) with multiple replicas.
- Cloud mappings (S3/Glue/EMR, ADF/ADLS/Synapse, Kafka/Event Hub streaming) are architecture targets: the ETL contract
  is identical in the Spark job; connectors are not included because they require paid cloud accounts.
- Not run in this environment: Airflow DAG, Kafka consumer, Hive DDL, Java client, Spark/Scala jobs, LoRA training, vLLM serving (code is provided; they need their runtimes).
- Streaming window aggregates are processing-time and additive, so replayed events can double-count windows (the `sales` table itself is idempotent).
