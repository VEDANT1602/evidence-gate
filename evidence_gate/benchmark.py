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
    configuration = result["configuration"]
    dev_count = result["metrics"]["dev"]["gated"]["cases"]
    report = f"""# Which policy passages can support a cited answer?

A support agent looking up a policy can find a passage that matches every keyword and still quote an obsolete rule. Both retrieval paths start from documents visible to the requester's tenant and role. Their difference is what happens to an accessible draft, expired policy, regional exception or conflict after that first filter.

The lexical baseline answered {baseline["answer_attempts"]} of {n} holdout requests. It cited inadmissible evidence {baseline["inadmissible_answer_count"]} times. The policy gate answered {gated["answer_attempts"]} requests and had {gated["inadmissible_answer_count"]} such citations. In this fixture, the expected behavior on {n-gated["required_answers"]} of the {n} cases was to clarify or abstain. Scoring only questions that received answers would discard those decisions.

| Holdout measure | Access-scoped BM25 | Policy gate |
| --- | ---: | ---: |
| Expected outcome and fact span | {baseline["task_successes"]}/{n} ({baseline["task_success_rate"]:.1%}) | {gated["task_successes"]}/{n} ({gated["task_success_rate"]:.1%}) |
| Requests answered | {baseline["answer_attempts"]}/{n} ({baseline["answer_coverage"]:.1%}) | {gated["answer_attempts"]}/{n} ({gated["answer_coverage"]:.1%}) |
| Correct among answers given | {baseline["correct_answers"]}/{baseline["answer_attempts"]} | {gated["correct_answers"]}/{gated["answer_attempts"]} |
| Answers citing inadmissible evidence | {baseline["inadmissible_answer_count"]} | {gated["inadmissible_answer_count"]} |

![Holdout outcome and coverage rates](quality.svg)

## What changes between the two paths

The baseline ranks authorized passages by BM25 and returns a positive lexical match. The gate first checks approval, the half-open validity window and the requested region. After ranking, it requires at least {configuration["minimum_coverage"]:.0%} query-token coverage. A missing regional qualifier can trigger a clarification. Contradictory active facts under the same managed claim key trigger abstention.

A returned answer is an exact stored span with a document citation. The [prediction log](predictions.jsonl) keeps the query, principal, expected result, cited document ID, quoted span and decision reason for each run. Filtering those records to one case ID gives the baseline and gated responses side by side. Cache lookup is disabled during quality evaluation.

| Holdout case type | Cases | Baseline correct | Gate correct |
| --- | ---: | ---: | ---: |
{categories}

The {result["documents"]} fictional documents and {dev_count} development cases exercise the same authored policy schema as the {n} held-out cases. The case mix deliberately emphasizes failure modes. Applying these rates to customer traffic would require independently collected questions, verified policy metadata and a reference answer for each. The source spans here are curated, so this experiment says nothing about a generative model's ability to write an accurate paraphrase.

## Reproduce the contract decisions

Run `python -m unittest discover -s tests -v` and `python -m evidence_gate benchmark` from the project root. Compare the fresh [metrics](metrics.json) with the [data card]({data_card}), [architecture]({architecture}) and [evaluation protocol]({evaluation}).

Independent experiment by Vedant Thaker. The conceptual references are [Build Your Own X](https://github.com/codecrafters-io/build-your-own-x), [RAG From Scratch](https://github.com/langchain-ai/rag-from-scratch), and [Robertson and Zaragoza on BM25](https://doi.org/10.1561/1500000019).
"""
    path.write_text(report, encoding="utf-8")
