# RERANK_EVAL

Dormant cross-encoder over hybrid top-50 candidates in `doc_index`.
Flag stays **`RERANK_ENABLED=false`**. Do not enable in production.

Gate to flip later: zero regressions on `tests/golden_set.yaml` +
B4/B5/A5 exact, and p95 latency budget held versus the flag-OFF run
(no separate numeric p95 budget is published in-repo; hold means
ON p95 must not exceed OFF p95 on these exact nodes).

Eval host: `3.11.16`. Embedder: `RAG_EMBEDDING_MODEL=fake`.
Repeats: 11 subprocess pytest invocations per node per flag.
Times are wall-clock per invocation (includes pytest startup).

## Run 1 — RERANK_ENABLED=false (default)

Flag: `RERANK_ENABLED=false`
Cross-encoder: not consulted (flag off)
Suite verdict: PASS
Repeats per node: 11

| node | verdict | median s | p95 s | min s | max s |
|---|---|---|---|---|---|
| B4 | PASS 11/11 | 0.906 | 0.942 | 0.895 | 0.942 |
| B5 | PASS 11/11 | 0.909 | 0.919 | 0.899 | 0.919 |
| B5_chat | PASS 11/11 | 0.925 | 0.946 | 0.906 | 0.946 |
| A5 | PASS 11/11 | 0.965 | 1.000 | 0.940 | 1.000 |

## Run 2 — RERANK_ENABLED=true (cross-encoder)

**UNPRODUCED** — real cross-encoder ON run UNPRODUCED: `sentence_transformers` is not in `requirements.txt` and is not installed in this environment. No numbers invented. Do not treat Run 2b as a flip-the-flag measurement.

## Run 2b — RERANK_ENABLED=true, model unavailable (degrade-to-cosine)

Flag: `RERANK_ENABLED=true`
Cross-encoder: degrade-to-cosine (sentence_transformers import failed: No module named 'sentence_transformers')
Suite verdict: PASS
Repeats per node: 11

| node | verdict | median s | p95 s | min s | max s |
|---|---|---|---|---|---|
| B4 | PASS 11/11 | 0.915 | 0.939 | 0.907 | 0.939 |
| B5 | PASS 11/11 | 0.911 | 0.916 | 0.902 | 0.916 |
| B5_chat | PASS 11/11 | 0.924 | 0.932 | 0.908 | 0.932 |
| A5 | PASS 11/11 | 0.944 | 0.950 | 0.939 | 0.950 |

## tests/golden_set.yaml

**UNPRODUCED** — `scripts/golden_set_gate.py` drives live
`POST /v1/agents/{agent}/chat/stream` against the fixture project.
This branch is not deployed (deploy SHA HOLD, live `567147a`).
A prod sweep would measure the live SHA, not this change. A local
sweep needs that corpus plus a funded chat key on this process.

## Production

`RERANK_ENABLED` remains `false`. Do not flip on.
