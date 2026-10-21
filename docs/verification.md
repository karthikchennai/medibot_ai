# Verification record

Verified on 2026-09-29. The app is built in the requested medibot_codex directory.

## Completed

- 109 backend tests passed. The one warning is a Starlette/httpx TestClient deprecation notice; it does not affect the running API. Machine-readable results: [test-results.xml](test-results.xml).
- Next.js production compilation, TypeScript validation and static page generation passed using Node 24.19.0 and Next.js 16.3.6. Worker-thread type checking avoids a subprocess restriction in this Windows environment; type checking remains enabled.
- Docling parsed all 12 documents into 250 chunks: billing 40, clinical 59, equipment 31, general 76, nursing 44. Tables, Markdown code, section headings, roles and page provenance were inspected. [Ingestion report](ingestion-report.json).
- Five real-index adversarial retrieval probes passed, independently of the UI refusal classifier. They used real embeddings and Qdrant and returned only authorized chunks. [Retrieval access evidence](../evidence/rbac-retrieval.json).
- Browser checks passed for nurse login, collection indicators, three actual API refusals, missing Groq-key guidance, logout and the 390px mobile layout. No browser console errors were recorded. No response mocking was used. [UI record](ui-smoke.json).
- `/health` returns HTTP 200 with `index_ready=true`, `database_ready=true`, and `groq_configured=false` until a key is provided.
- The launcher successfully started the local FastAPI and production Next.js processes.

## Measured retrieval quality

The 25 manually grounded questions cover every supplied document and all five collections. Same corpus, access rules, annotations and top-three output budget:

| Method | MRR@3 | Answer-bearing passage in top 3 |
|---|---:|---:|
| Dense only | 0.7800 | 84% |
| Hybrid only | 0.8133 | 88% |
| Dense + cross-encoder | 0.8800 | 92% |
| Hybrid + cross-encoder | 0.8800 | 92% |

Both dense and hybrid candidate sets had 96% annotated-passage recall at 10. Hybrid plus reranking improved over dense-only on this development set; it tied dense plus the same reranker. These results do not establish general superiority, statistical significance or clinical accuracy. The phrase-based annotations are not an exhaustive relevance judgment.

The benchmark ran against a file copy of the completed index while the app retained the original index open. No documents or vectors changed during copying or evaluation. Qdrant points, model settings and annotations were unchanged. Stop the backend before future evaluation against the original embedded index.

[Full measured run](../evidence/retrieval.json) includes the candidate and reranked passages, per-question metrics and source hashes. [Four baseline comparisons](../evidence/baselines.json) are computed from that same saved run with no changed annotations or rerun queries.

## Pending your Groq key

`.env` and `.env.example` contain a blank `GROQ_API_KEY`. Add the key locally and restart the backend. Live Groq generation has not been tested: no substitute responses are presented as live model evidence.

Four SQL reference cases were verified directly against the real database, and the SQL unit tests check the pipeline with explicitly labelled model test doubles. After configuration, run from `backend`:

```powershell
..\.venv\Scripts\python.exe -m evaluation.live_smoke
```

This tests the four natural-language SQL questions through the actual running API and compares generated-query results with the reference rows. It currently exits with a clear missing-key message. Also try a permitted document question and inspect its source citations.

## Local process note

Windows denied the automated process-tree stop operation inside the restricted Codex execution environment. The app remains running. Run `stop.ps1` and `start.ps1` from your own PowerShell terminal when restarting after editing `.env`. The stop script verifies saved process start times before terminating them. No unrelated process was stopped.

Public repository publication and assignment-dashboard submission have not been performed.
