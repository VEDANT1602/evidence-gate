# Evidence Gate

**An answer should survive an evidence check before it reaches the user.**

I built this reference system to examine a narrow applied-AI problem: a passage can be highly relevant and still be an unusable basis for an answer. It may be expired, unapproved, from the wrong region, or outside the requester's permissions.

The project pairs an original BM25 implementation with an explicit evidence contract. It runs entirely on Python's standard library. The baseline and the proposed system share the same authorization boundary; the experiment isolates what metadata gates and abstention add after that boundary.

**New independent portfolio work, not a historical client delivery.** All policy documents and queries are fictional. The evaluated answers are extracted spans, not generated text. This repository does not claim measured LLM performance or reproduce an employer's proprietary system.

## Read the engineering story

Start with the [generated case study](results/case-study.md): the decision, common approach, controlled failure, framework, measured result, tradeoff, and next action.

On the fixed 24-case holdout fixture, Evidence Gate produced 24 correct answer/clarify/abstain decisions. The relevance-only ablation produced 5. That is a regression-suite result on constructed stress cases, not an estimate of customer accuracy. The complete [metrics](results/metrics.json) and [per-case predictions](results/predictions.jsonl) are included.

![Measured comparison on the fixed fictional suite](results/quality.svg)

## Run it in under a minute

Requirements: Python 3.10 or newer. No pip installation, model download, API key or network connection is needed for the core project.

```bash
# Run from this project directory.
python -m unittest discover -s tests -v
python -m evidence_gate benchmark
python -m evidence_gate query "refund eligibility window" --region us
python -m evidence_gate query "refund eligibility window"
python -m evidence_gate query "enterprise export approval reviewers" --region us
```

The first query returns the approved current US policy with a source citation. The second requests a region. The third abstains because two currently approved records disagree.

```json
{
  "status": "answered",
  "answer": "Refund eligibility lasts 30 days after purchase.",
  "reason": "verbatim_evidence",
  "citations": [{
    "document_id": "northstar-refund-us-current",
    "source": "fixture://northstar/refund/us/current",
    "quote": "Refund eligibility lasts 30 days after purchase.",
    "valid_from": "2026-01-01"
  }],
  "cache_hit": false
}
```

The `fixture://` URI denotes a record in the included original corpus, not a link to an external customer system.

## What is implemented

- BM25 scoring, deterministic tie-breaking and inspectable tokenization.
- Tenant and role filtering before ranking in both comparators.
- Approval, validity-date and geographic-scope constraints.
- Explicit `answered`, `clarify` and `abstain` response contracts.
- Verbatim citations and conflict checks over curated claim keys.
- A bounded, thread-safe cache isolated by tenant, roles, region, date and corpus version.
- A loopback HTTP API that derives the principal from a server-side bearer-token map, not from request body fields.
- An original versioned dataset, fixed development/holdout cases, raw predictions, generated report and SVG chart.
- Tests covering authorization, cross-tenant and role-sensitive cache behavior, future/expired documents, conflicting evidence, API identity escalation, and numerical reconciliation.

## Use the local API

The API requires an explicit token-to-principal map and binds only to `127.0.0.1`. The example credential below is a public demo placeholder for fictional data. Replace it locally; do not use it for private documents or commit actual credentials.

PowerShell:

```powershell
$env:EVIDENCE_GATE_TOKENS = '{"replace-this-local-demo-token":{"tenant":"northstar","roles":["reader"]}}'
python -m evidence_gate serve --port 8080
```

In another terminal:

```powershell
$headers = @{ Authorization = 'Bearer replace-this-local-demo-token' }
$body = @{ text = 'refund eligibility window'; region = 'us' } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8080/v1/query -Headers $headers -ContentType application/json -Body $body
```

The endpoint rejects client-supplied roles, tenant fields, or baseline mode. A health endpoint is available at `GET /health`.

This is a local demo, not a production authentication service. TLS, credential rotation, identity lifecycle, rate limiting, shared cache infrastructure and operational deployment are outside the implemented scope.

## Inspect the data and evaluation

| Path | Purpose |
|---|---|
| [`data/corpus.jsonl`](data/corpus.jsonl) | 58 fictional policy records with approval, validity, scope and access metadata |
| [`data/cases.json`](data/cases.json) | 8 development cases and 24 fixed holdout cases |
| [`data/DATA_CARD.md`](data/DATA_CARD.md) | Provenance, schema, rights and limitations |
| [`docs/architecture.md`](docs/architecture.md) | Trust boundary, caching, failure behavior and production gaps |
| [`docs/evaluation.md`](docs/evaluation.md) | Metrics, denominators and interpretation |
| [`results/metrics.json`](results/metrics.json) | Calculated results, configuration and input SHA-256 hashes |
| [`results/predictions.jsonl`](results/predictions.jsonl) | Every query, expectation, response and scoreable outcome |

Regenerate the same fixture with `python -m evidence_gate fixture`. The benchmark overwrites only its generated result files. `--data PATH` is a global flag and belongs before the command; `--output PATH` is a benchmark flag.

## What I would add before a real pilot

An independently authored customer query set, a real identity provider, audited document metadata, a reviewed definition of validity and scope, and separate evaluation of any proposed generative model. This version deliberately leaves embeddings and LLM generation out of the measured claim. A model adapter would require its own provenance, cost, latency and grounded-answer evaluation.

## Inspiration and license

[Build Your Own X](https://github.com/codecrafters-io/build-your-own-x) and its [RAG From Scratch](https://github.com/langchain-ai/rag-from-scratch) reference inspired the learning direction. The code, corpus, query cases and benchmark results here were written for this project; tutorial code and results were not copied.

The primary retrieval-method reference is Robertson and Zaragoza, [The Probabilistic Relevance Framework: BM25 and Beyond](https://doi.org/10.1561/1500000019). The nonnegative IDF variant and fixed constants used here are documented in the evaluation protocol.

Code and the original fictional data are available under the [MIT license](LICENSE).
