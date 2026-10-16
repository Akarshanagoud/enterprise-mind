# Contributors

EnterpriseMind is maintained by two people. Each owns one half of the codebase and reviews the other's work.

| Maintainer | GitHub | Owns |
|---|---|---|
| **Akarsha Namachanpally** | [@Akarshanagoud](https://github.com/Akarshanagoud) | AI & Data Engineering |
| **Virinchi Sai** | [@virinchisai](https://github.com/virinchisai) | Security, Governance, Quality & Platform |

## Who owns what

**Akarsha: AI & Data Engineering.** Everything that turns documents and data into answers.

| Area | Paths |
|---|---|
| RAG: ingestion, chunking, embeddings, hybrid retrieval, LLM providers, answer pipeline | `src/emind/rag/`, `src/emind/text.py` |
| Agent and text-to-SQL | `src/emind/agent.py` |
| Structured output extraction | `src/emind/extraction.py` |
| Batch ETL, CDC, streaming, warehouse analytics | `src/emind/etl/`, `src/emind/streaming.py`, `src/emind/analytics.py` |
| Spark, Scala, Hive, T-SQL, Airflow, Java client | `scala/`, `hive/`, `sql/`, `dags/`, `java/` |
| Fine-tuning (LoRA / QLoRA) | `scripts/` |
| Knowledge content | `data/sample_docs/` |

**Virinchi: Security, Governance, Quality & Platform.** Everything that keeps those answers safe, measurable and deployable.

| Area | Paths |
|---|---|
| Guardrails, PII redaction, grounding checks, audit chain, governance metrics | `src/emind/governance/` |
| API security (auth, RBAC, rate limiting, headers) and configuration | `src/emind/api.py`, `src/emind/config.py` |
| Evaluation and the golden question set | `src/emind/evaluation.py`, `data/golden.jsonl` |
| Test suite | `tests/` |
| Web console | `src/emind/static/` |
| Containers, Kubernetes, CI/CD | `Dockerfile`, `docker-compose.yml`, `deploy/`, `.github/` |

**Shared:** `README.md`, this file, `pyproject.toml`, `src/emind/__main__.py`, `src/emind/models.py`.

[`.github/CODEOWNERS`](.github/CODEOWNERS) encodes this table, so GitHub automatically requests the owner's review on every pull request that touches their paths.

## Working agreement

- Branch from `main` as `feat/<area>-<topic>` or `fix/<area>-<topic>`; never push directly to `main`.
- Every pull request needs one approving review from the other maintainer before it merges.
- CI must pass: the test suite and the golden-set evaluation gate.
- A change that lowers any golden-set score must explain why in the pull request.
- Anything touching authentication, access control, guardrails or which documents a role can read is always reviewed by Virinchi. Changes to the warehouse schema or the retrieval pipeline are always reviewed by Akarsha.
- Keep the default path free of API keys, paid services and hosted dependencies.
- New endpoints are written by whoever builds the feature; because `api.py` belongs to Virinchi, every change to the API surface gets a security review.

## Backlog

### Akarsha: AI & Data Engineering
1. Benchmark `EMIND_EMBEDDER=sbert` with FAISS against the default LSA embeddings on the golden set and publish the comparison.
2. Run a local LLM end to end (Ollama `llama3.2`), tune the prompt, and keep the extractive fallback.
3. Replace the follow-up heuristic with LLM query rewriting behind a feature flag.
4. Agent: chronological time series ("revenue by month"), date filters ("last quarter") and more metrics.
5. Add Kafka and Airflow to `docker-compose.yml`; run the streaming consumer and the daily DAG end to end.
6. A vector-store adapter (pgvector or Chroma) for corpora beyond about 100k chunks.
7. A LoRA fine-tuning run on an instruction set generated from the documents.

### Virinchi: Security, Governance, Quality & Platform
1. Replace static API keys with OIDC/JWT single sign-on, and stop keeping the key in browser storage.
2. Redis-backed rate limiting and metrics so several replicas share state.
3. Grow the golden set to 50+ questions, including a jailbreak and prompt-injection suite.
4. Playwright end-to-end tests for the console: sign-in per role, asking, uploading, analytics, governance.
5. CI hardening: ruff, pip-audit, CodeQL, and building and publishing the container image.
6. Ship the audit log to a SIEM and schedule a hash-chain verification job.
7. More DLP detectors (IBAN, passport numbers, cloud access keys) and a configurable output policy.
