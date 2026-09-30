# Requirements

## Purpose

These requirements define the engineering standard for the project, independent of its problem domain. They cover a production-quality AI application: data ingestion, retrieval, model integration, agents, guardrails, evaluation, observability, security, and operations. Each requirement has acceptance criteria and a note on the evidence that shows it is met.

Commits and pull requests reference requirements by ID (for example, `Implements ING-3`).

Priority key:

- **Must**: required for the project to meet its core goals.
- **Should**: expected, and implemented unless there is a documented reason not to.
- **Could**: optional, implemented only if time allows.

---

## 1. Data ingestion

| ID | Priority | Requirement |
|---|---|---|
| ING-1 | Must | Load data from at least one external API and at least one file format. |
| ING-2 | Must | Cache external API responses locally and handle retries and rate limits. |
| ING-3 | Must | Ingestion is idempotent: re-running it creates no duplicates and does not re-embed unchanged records (content hashing). |
| ING-4 | Should | Ingestion reports counts of added, updated, unchanged, and removed records. |

**Acceptance:** running ingestion twice in a row results in zero records re-processed on the second run.

**Evidence:** terminal output of two consecutive runs.

---

## 2. Retrieval (RAG)

| ID | Priority | Requirement |
|---|---|---|
| RAG-1 | Must | Semantic search over structured records, combined with exact metadata filters. |
| RAG-2 | Must | Long unstructured documents are chunked, embedded, and retrievable. Chunking strategy is documented and justified. |
| RAG-3 | Must | Answers generated from retrieved content include citations pointing to the source chunk or section. |
| RAG-4 | Must | When the retrieved content does not answer the question, the system says so rather than inventing an answer. |
| RAG-5 | Should | Hybrid search (keyword plus vector), with the eval suite showing whether it beats vector-only search. |
| RAG-6 | Could | A reranking step, evaluated against the non-reranked baseline. |

**Acceptance:** citations resolve to the correct source; out-of-scope questions return an explicit "not found" response.

**Evidence:** one answered question with citations, one out-of-scope question correctly declined.

---

## 3. Model integration

| ID | Priority | Requirement |
|---|---|---|
| MOD-1 | Must | All model calls go through one internal interface the project owns. |
| MOD-2 | Must | Structured outputs are validated against a schema (e.g. Pydantic); invalid output is retried or rejected, never passed through. |
| MOD-3 | Must | Every model call has a timeout, retry policy, and token budget. |
| MOD-4 | Should | Providers can be swapped by configuration, including at least one hosted API and one locally run model. |
| MOD-5 | Should | The eval report compares results across providers. |

**Acceptance:** switching provider requires a config change only, no code change.

**Evidence:** the same request run on two providers, results side by side.

---

## 4. Agents and tools

| ID | Priority | Requirement |
|---|---|---|
| AGT-1 | Must | The agent uses native tool/function calling, not a custom text protocol. |
| AGT-2 | Must | The agent loop is bounded by a maximum number of turns and a token or cost budget. |
| AGT-3 | Must | Application state is owned by code; the model changes state only through tools. |
| AGT-4 | Must | Invalid tool calls (bad arguments, unknown tool, disallowed action) return an error to the model instead of crashing the run. |
| AGT-5 | Should | The same tools are exposed as an MCP server usable from an MCP client (e.g. Claude Desktop or Claude Code). |
| AGT-6 | Could | A second implementation of the agent on an agent framework, with a written comparison to the hand-built loop. |

**Acceptance:** a run that hits the turn limit ends cleanly and saves partial results; an invalid tool call is recovered from within the same run.

**Evidence:** a trace of a multi-step task; a screen recording of the MCP server used from an MCP client.

---

## 5. Guardrails and human oversight

| ID | Priority | Requirement |
|---|---|---|
| GRD-1 | Must | Deterministic validation checks every model proposal before it takes effect. The model proposes; code decides. |
| GRD-2 | Must | Any action with side effects (writing, modifying, exporting data) requires explicit user approval. |
| GRD-3 | Must | Retrieved documents and user input are treated as untrusted; system instructions cannot be overridden by content. |
| GRD-4 | Should | The eval suite includes prompt-injection test cases. |
| GRD-5 | Should | Every approval, rejection, and executed action is recorded in an audit log. |

**Acceptance:** an invalid proposal is rejected by validation with a clear reason; no side effect occurs without an approval record.

**Evidence:** agent proposes a change, validation rejects an invalid one, user approves a valid one, audit log shows both.

---

## 6. Evaluation

| ID | Priority | Requirement |
|---|---|---|
| EVL-1 | Must | A labelled retrieval test set, reporting recall@k and MRR. |
| EVL-2 | Must | A labelled answer test set, reporting correctness and citation accuracy. |
| EVL-3 | Must | Model-graded evals use a written rubric, and a sample of grades is checked by hand, with agreement rate reported. |
| EVL-4 | Must | Task-level metrics per run: success rate, cost, latency, number of turns. |
| EVL-5 | Must | A small, low-cost regression eval runs in CI and fails the build if results drop below a threshold. |
| EVL-6 | Should | Eval reports compare variants (prompt versions, models, retrieval methods) and are saved with a timestamp. |

**Acceptance:** a deliberately degraded prompt or retrieval setting causes the CI eval job to fail.

**Evidence:** eval results table in the README; a link to a CI run that failed on a regression.

---

## 7. Observability

| ID | Priority | Requirement |
|---|---|---|
| OBS-1 | Must | Every model call and tool call is logged with arguments, tokens, cost, latency, and outcome. |
| OBS-2 | Must | Each request has a trace ID linking all of its model and tool calls. |
| OBS-3 | Should | A dashboard shows cost, latency, and success rate over time. |
| OBS-4 | Should | Alerts or visible flags when cost or error rate exceeds a threshold. |

**Acceptance:** any single request can be reconstructed end to end from its trace ID.

**Evidence:** dashboard screenshot and one full request trace.

---

## 8. Security and access control

| ID | Priority | Requirement |
|---|---|---|
| SEC-1 | Must | No secrets in the repository; secrets come from environment or a secrets manager. |
| SEC-2 | Must | CI includes secret scanning and dependency vulnerability scanning. |
| SEC-3 | Should | Users are authenticated and can access only their own data. |
| SEC-4 | Should | API keys are scoped to specific permissions. |
| SEC-5 | Must (if public) | Any public deployment has rate limiting and a hard spending cap. |

**Acceptance:** a request for another user's data is refused; a request over the rate limit is rejected.

**Evidence:** the refused request and its log entry.

---

## 9. Engineering quality

| ID | Priority | Requirement |
|---|---|---|
| ENG-1 | Must | Typed Python, checked with mypy; linted and formatted with ruff. |
| ENG-2 | Must | Unit tests for all deterministic logic, with model calls mocked. No paid API calls in unit tests. |
| ENG-3 | Must | Integration tests against a real database (e.g. PostgreSQL with pgvector) in a container. |
| ENG-4 | Must | Tests are written before implementation for each feature (test-first). |
| ENG-5 | Should | An HTTP API with generated OpenAPI documentation. |
| ENG-6 | Must | A hand-written Dockerfile, and Docker Compose bringing up the full stack with one command. |
| ENG-7 | Must | CI runs lint, type check, unit tests, integration tests, the regression eval, security scans, and an image build. |
| ENG-8 | Should | Work is done through pull requests with descriptions, and tracked in issues. |

**Acceptance:** a clean clone passes CI with no manual steps.

---

## 10. Deployment

| ID | Priority | Requirement |
|---|---|---|
| DEP-1 | Should | Deployed to a cloud platform (GCP preferred). |
| DEP-2 | Should | Infrastructure defined as code. |
| DEP-3 | Should | Health check endpoint and a documented rollback procedure. |
| DEP-4 | Could | Run and eval logs exported to a data warehouse (e.g. BigQuery) for analysis. |

**Acceptance:** the deployed service passes its health check and can be rolled back to the previous version.

---

## 11. Demo and documentation

| ID | Priority | Requirement |
|---|---|---|
| DEM-1 | Must | `docker compose up` plus a seed script gives a working local demo with sample data. |
| DEM-2 | Must | A demo mode that works without a user-supplied API key (local model or recorded model responses). |
| DEM-3 | Must | README covering: what the project does, architecture diagram, step-by-step demo script, eval results, design decisions and reasons, known limitations. |
| DEM-4 | Should | A 3 to 5 minute recorded walkthrough. |
| DEM-5 | Should | A live demo link, protected by SEC-5. |
| DEM-6 | Could | A short write-up of one finding from the evals. |

**Acceptance:** someone unfamiliar with the project can follow the README demo script from a clean clone in under 15 minutes.

---

## Priority summary

If time is limited, the core of the project is:

1. Retrieval (RAG-1 to RAG-4)
2. Agents and tools (AGT-1 to AGT-4)
3. Guardrails (GRD-1 to GRD-3)
4. Evaluation (EVL-1 to EVL-5)

Everything else is supporting work.
