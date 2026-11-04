# Evaluation protocol

## Question

Can metadata gates and an explicit answer contract prevent the deliberately constructed stale/draft/scope failures that relevance ranking alone does not address?

## Comparator

The baseline and Evidence Gate use the same BM25 formula and tenant/role filter. The baseline ignores approval, validity and geographic scope and returns the top positively matched record. It is a relevance-only ablation, not a state-of-the-art RAG baseline. The gated variant filters metadata, requires at least 0.30 query-token coverage, and checks the selected managed claim for missing regional scope and conflicting approved facts.

BM25 uses `k1=1.2`, `b=0.75`, unique query tokens and `log(1+(N-df+0.5)/(df+0.5))`. Document text is title plus content. Candidate-set statistics are calculated after authorization filtering. Ties are resolved by document ID. The small stopword set and all constants are explicit in code.

## Fixed cases and threshold discipline

Eight development cases exercise the interface. Twenty-four cases are preassigned to holdout, spanning current policies, missing scope, contradictory sources, unknown queries, role boundaries and tenant isolation. The code does not search thresholds or modify cases based on model predictions. The fixed 0.30 token-coverage rule is a design choice, not an empirically optimal threshold.

The corpus and test author are the same. Several holdout questions are close paraphrases of development topics. This is an adversarial regression suite, not a blind external test set. A high score supports the specified contract behavior; it is insufficient evidence for real-world accuracy.

## Metrics and denominators

| Metric | Numerator | Denominator |
|---|---|---|
| Task success | Exact expected status AND exact expected answer agree | All cases in the split |
| Answer coverage | Responses with status `answered` | All cases in the split |
| Answerable-case accuracy | Correct answer responses | Cases whose expected status is `answered` |
| Selective answer accuracy | Correct answer responses | Responses whose actual status is `answered` |
| Inadmissible answer count | Answers citing unapproved, out-of-date, future or wrong-region evidence | Count, not a rate |

The exact answer span is an authored fixture fact, not an LLM-judged score. `clarify` and `abstain` cases expect a null answer. No passage retrieval score is reported as an answer-quality score. No causal, financial, statistical-significance, model-quality or production-latency conclusion is drawn.

## Evidence retention and checks

`predictions.jsonl` retains each case, split, identity context, expected result, response, exact citations and Boolean scoring decisions. The benchmark disables caching for quality runs. `metrics.json` aggregates those same rows. The report and chart are generated from that metrics object and do not recalculate percentages independently.

Unit and HTTP integration tests separately check cache scope, date boundaries, credential handling, forbidden identity overrides, query limits, and aggregation reconciliation. Those tests are useful engineering evidence; they do not prove the absence of all security defects.

## Re-run

```bash
python -m unittest discover -s tests -v
python -m evidence_gate benchmark
```

For an alternate original corpus: `python -m evidence_gate --data path/to/data benchmark --output path/to/results`. Loading rejects duplicate document identifiers, invalid validity intervals, missing required fields and claims that are not verbatim spans.
