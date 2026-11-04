"""Evaluate fixed cases, retain every prediction, and derive narrative/figures."""
from __future__ import annotations

from datetime import date
from hashlib import sha256
from html import escape
import json
import os
from pathlib import Path

from .core import EvidenceGate, Principal, load_documents


def evaluate(gate, cases, mode):
    rows = []
    documents = {d.id: d for d in gate.documents}
    for case in cases:
        response = gate.query(case["query"], Principal(case["tenant"], tuple(case["roles"])),
                              region=case["region"], as_of=case["as_of"], mode=mode)
        correct = response.status == case["expected_status"] and response.answer == case["expected_answer"]
        admissible = all(documents[c.document_id].approved and documents[c.document_id].current(date.fromisoformat(case["as_of"]))
                         and documents[c.document_id].region in {"global", case["region"]}
                         for c in response.citations)
        rows.append({**case, "mode": mode, "response": response.to_dict(), "task_success": correct,
                     "answered": response.status == "answered",
                     "evidence_admissible": admissible if response.citations else None})
    return rows


def summarize(rows):
    n = len(rows)
    attempts = sum(r["answered"] for r in rows)
    correct_answers = sum(r["task_success"] and r["answered"] for r in rows)
    required_answers = sum(r["expected_status"] == "answered" for r in rows)
    return {
        "cases": n, "task_successes": sum(r["task_success"] for r in rows),
        "task_success_rate": sum(r["task_success"] for r in rows) / max(n, 1),
        "answer_attempts": attempts, "answer_coverage": attempts / max(n, 1),
        "correct_answers": correct_answers, "required_answers": required_answers,
        "answerable_case_accuracy": correct_answers / max(required_answers, 1),
        "selective_answer_accuracy": correct_answers / max(attempts, 1),
        "inadmissible_answer_count": sum(r["evidence_admissible"] is False for r in rows),
    }


def run(directory: Path, output: Path):
    documents = load_documents(directory / "corpus.jsonl")
    cases = json.loads((directory / "cases.json").read_text(encoding="utf-8"))
    if len({c["id"] for c in cases}) != len(cases):
        raise ValueError("Case ids must be unique")
    if {c["split"] for c in cases} != {"dev", "holdout"}:
        raise ValueError("Both predetermined splits are required")
    gate = EvidenceGate(documents, cache_size=0)
    rows = [r for mode in ("baseline", "gated") for r in evaluate(gate, cases, mode)]
    metrics = {}
    for split in ("dev", "holdout"):
        metrics[split] = {mode: summarize([r for r in rows if r["split"] == split and r["mode"] == mode])
                          for mode in ("baseline", "gated")}
    categories = {}
    for category in sorted({c["category"] for c in cases if c["split"] == "holdout"}):
        categories[category] = {mode: summarize([r for r in rows if r["split"] == "holdout" and r["category"] == category and r["mode"] == mode])
                                for mode in ("baseline", "gated")}
    hashes = {name: sha256((directory / name).read_bytes()).hexdigest() for name in ("corpus.jsonl", "cases.json")}
    result = {"protocol_version": 1, "data_kind": "original_fictional_fixture", "documents": len(documents),
              "corpus_version": gate.version, "input_sha256": hashes,
              "configuration": {"bm25_k1": 1.2, "bm25_b": 0.75, "minimum_coverage": 0.30,
                                "as_of": "2026-11-04", "cache_disabled_for_quality_evaluation": True},
              "metrics": metrics, "holdout_categories": categories}
    output.mkdir(parents=True, exist_ok=True)
    (output / "metrics.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (output / "predictions.jsonl").write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows), encoding="utf-8")
    render_chart(result, output / "quality.svg")
    render_report(result, output / "case-study.md")
    return result


def render_chart(result, path):
    rows = [("Correct contract outcomes", "task_success_rate"), ("Requests answered", "answer_coverage"),
            ("Correct among answers", "selective_answer_accuracy")]
    colors = {"baseline": "#718096", "gated": "#087e8b"}
    x, width = 270, 370
    svg = ['<svg xmlns="http://www.w3.org/2000/svg" width="760" height="355" viewBox="0 0 760 355">',
           '<rect width="760" height="355" fill="#fff"/>',
           '<g font-family="sans-serif" fill="#172b4d">',
           '<text x="24" y="30" font-size="18" font-weight="bold">Authority gates change the answer/abstain decision</text>',
           '<text x="24" y="53" font-size="12">Fixed fictional holdout suite. Percent of cases; answer accuracy uses answered cases only.</text>']
    for tick in (0, 25, 50, 75, 100):
        xx = x + width * tick / 100
        svg.append(f'<line x1="{xx}" y1="72" x2="{xx}" y2="285" stroke="#e2e8f0"/>')
        svg.append(f'<text x="{xx}" y="305" text-anchor="middle" font-size="11">{tick}%</text>')
    for i, (label, key) in enumerate(rows):
        y = 84 + i * 72
        svg.append(f'<text x="24" y="{y+23}" font-size="13">{escape(label)}</text>')
        for j, mode in enumerate(("baseline", "gated")):
            value = result["metrics"]["holdout"][mode][key]
            yy = y + j * 24
            svg.append(f'<rect x="{x}" y="{yy}" width="{width*value:.2f}" height="18" fill="{colors[mode]}"/>')
            svg.append(f'<text x="{x+width*value+7:.2f}" y="{yy+14}" font-size="12">{value:.1%}</text>')
    svg.extend(['<rect x="24" y="324" width="12" height="12" fill="#718096"/><text x="43" y="334" font-size="12">ACL-aware relevance baseline</text>',
                '<rect x="285" y="324" width="12" height="12" fill="#087e8b"/><text x="304" y="334" font-size="12">Evidence Gate</text>', '</g></svg>'])
    path.write_text("\n".join(svg) + "\n", encoding="utf-8")


def render_report(result, path):
    project = Path(__file__).resolve().parents[1]

    def document_link(relative):
        source = (project / relative).resolve()
        try:
            return Path(os.path.relpath(source, path.parent.resolve())).as_posix()
        except ValueError:
            # Windows cannot form a relative path across volumes. A local file
            # URI remains usable when the report is deliberately written there.
            return source.as_uri()

    data_card = document_link("data/DATA_CARD.md")
    architecture = document_link("docs/architecture.md")
    evaluation = document_link("docs/evaluation.md")
    baseline, gated = (result["metrics"]["holdout"][m] for m in ("baseline", "gated"))
    n = gated["cases"]
    categories = "\n".join(f'| {category.replace("_", " ")} | {m["gated"]["cases"]} | {m["baseline"]["task_successes"]} | {m["gated"]["task_successes"]} |'
                           for category, m in result["holdout_categories"].items())
    report = f'''# Evidence Gate: relevance is only one part of a usable answer

**Independent portfolio reference implementation by Vedant Thaker.** This is a newly built public engineering experiment on original fictional data. It does not reproduce a CSAA system, disclose client documents, or verify historical workplace metrics. The evaluated system extracts managed fact spans; no language model was evaluated.

## The decision I started with

Should a support assistant answer because it found a similar document, or only when that document is authorized, approved, current, and specific enough for the request?

I treated the answer as a decision contract. The requester needs a usable policy fact, an exact citation, or an explicit request for clarification. A confident answer from an expired policy is a failure even if the retrieval score is high.

## Topic: document retrieval. Subtopic: admissible evidence

A relevance-only workflow ranks passages and returns the highest match. This experiment implements BM25 from scratch as that comparator. Both systems filter tenant and role permissions **before** scoring. I did not create an artificially unsafe authorization baseline to make the proposed design look better.

The comparator's deliberate limitation is narrower: it ignores approval, validity dates and geographic scope, then answers whenever a positive lexical match exists. It is an ablation, not a claim that all conventional RAG products behave this way.

## I made the failure observable before adding a model

The corpus contains {result["documents"]} authored policy documents across two fictional tenants. Expired policies and unapproved drafts repeat query terms. Current US and EU refund policies differ. Two simultaneously approved export policies contradict each other. Administrator-only material tests a separate role boundary.

Those are constructed stress cases, not a representative sample of customer traffic. I used eight development cases to exercise the interface and a preassigned {n}-case holdout suite for the reported comparison. The threshold and formulas are explicit in `metrics.json`; no threshold search is performed. Because the same author designed the corpus and cases, the split is a reproducibility control, not an independent generalization study.

## My framework: scope, retrieve, validate, answer or abstain

1. Establish the principal outside the query text; scope candidate documents by tenant and roles.
2. Apply approval, half-open validity intervals and an explicit region facet before ranking.
3. Rank the remaining documents with the same BM25 implementation.
4. Require minimum query-token coverage. If a selected managed claim has several regions and none was supplied, request clarification. If authoritative claims disagree, abstain.
5. Return a verbatim span with its source and effective date. Key the bounded cache by tenant, roles, region, date, mode, corpus hash and query.

The claim keys and verbatim fact spans are curated schema fields. This project does not solve arbitrary semantic conflict detection or free-form answer synthesis. Keeping that boundary explicit makes the implementation inspectable.

```mermaid
flowchart LR
    A[Trusted principal and query] --> B[Tenant and role scope]
    B --> C[Approval, date and region filters]
    C --> D[BM25 retrieval]
    D --> E{{Evidence contract}}
    E --> F[Verbatim answer with citation]
    E --> G[Clarify region or abstain]
    F --> H[Principal-scoped bounded cache]
    G --> H
```

## The measured result changed the decision, not just the rank

| Holdout measure | ACL-aware relevance baseline | Evidence Gate |
|---|---:|---:|
| Correct answer/clarify/abstain outcomes | {baseline["task_successes"]}/{n} ({baseline["task_success_rate"]:.1%}) | {gated["task_successes"]}/{n} ({gated["task_success_rate"]:.1%}) |
| Requests answered | {baseline["answer_attempts"]}/{n} ({baseline["answer_coverage"]:.1%}) | {gated["answer_attempts"]}/{n} ({gated["answer_coverage"]:.1%}) |
| Correct answers among answer attempts | {baseline["correct_answers"]}/{baseline["answer_attempts"]} ({baseline["selective_answer_accuracy"]:.1%}) | {gated["correct_answers"]}/{gated["answer_attempts"]} ({gated["selective_answer_accuracy"]:.1%}) |
| Answers using inadmissible evidence | {baseline["inadmissible_answer_count"]} | {gated["inadmissible_answer_count"]} |

![Measured holdout comparison](quality.svg)

Task success requires exact agreement with both the expected status and fact span. Coverage is answered requests divided by all {n} holdout requests. Selective accuracy uses answered requests as its denominator. An inadmissible answer cites an unapproved, expired, future, or wrong-region document. These metrics are not LLM hallucination or factual-generalization scores.

| Holdout category | Cases | Baseline correct | Gate correct |
|---|---:|---:|---:|
{categories}

Every numerator is reconstructible from `predictions.jsonl`. The benchmark disables the cache for quality evaluation; it cannot win by reusing development answers.

## What the result supports, and what it does not

The observed improvement supports a small engineering decision: in this managed policy fixture, metadata gates and explicit abstention prevent the constructed stale/draft/scope failures. The main tradeoff is reduced answer coverage and additional document-governance work. Requiring a region creates friction when the caller could have supplied one up front.

There is insufficient evidence to estimate production accuracy, latency, financial savings, user satisfaction, or LLM behavior. No paid API, GPU benchmark, customer contract or employer system was used. A perfect result on a small author-written suite is not a deployment approval.

The main alternative is relevance ranking followed by a stronger generative model. I did not run that alternative, so I make no comparative model-quality claim. A stronger model might recognize some policy conflicts, but it still needs trusted identity and version metadata; that is a design inference to test, not a measured result here.

## The next decision I would take to a customer

Keep this as a reference implementation. Before a pilot, obtain permissioned documents and independent queries, test identity integration, review region/validity semantics with policy owners, and evaluate any proposed language model separately. Measure correct supported answers, unnecessary abstention, unauthorized retrieval attempts, document-update lag and end-to-end latency. Revisit the design if document metadata is incomplete or users cannot provide required scope.

My working style is reflected in the artifact: define the decision and denominators first; compare a simple alternative; retain per-case evidence; challenge what the numbers establish; and make the next action proportionate to that evidence.

## Reproduce and inspect

From this project directory: `python -m unittest discover -s tests -v`, then `python -m evidence_gate benchmark`. This report and the chart are generated from the same metrics object as `metrics.json`. Data hashes are recorded there. See the [data card]({data_card}), [architecture]({architecture}) and [evaluation protocol]({evaluation}).

## References and originality

- [Build Your Own X](https://github.com/codecrafters-io/build-your-own-x) supplied the from-scratch learning direction.
- [RAG From Scratch](https://github.com/langchain-ai/rag-from-scratch) supplied conceptual context for indexing, retrieval and grounded generation. No tutorial code, datasets or reported results were copied.
- The BM25 formula is a standard information-retrieval method; the implementation, fictional corpus, query suite, contract layer, API, tests and narrative in this project were written for this repository.
- Robertson and Zaragoza's [The Probabilistic Relevance Framework: BM25 and Beyond](https://doi.org/10.1561/1500000019) is a primary reference for the retrieval method. The nonnegative IDF variant and constants used here are stated in the evaluation protocol.
'''
    path.write_text(report, encoding="utf-8")
