from dataclasses import replace
import json
from pathlib import Path
import re
import tempfile
import unittest

from evidence_gate.core import BM25, Document, EvidenceGate, Principal, load_documents
from evidence_gate.fixture import build_cases, build_corpus, write_fixture
from evidence_gate.benchmark import run


READER = Principal("northstar", ("reader",))
ADMIN = Principal("northstar", ("admin",))


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.documents = build_corpus()
        self.gate = EvidenceGate(self.documents)

    def test_current_approved_policy_beats_lexically_repeated_draft(self):
        baseline = self.gate.query("refund eligibility window", READER, region="us", mode="baseline")
        gated = self.gate.query("refund eligibility window", READER, region="us")
        self.assertEqual(baseline.answer, "Refund eligibility lasts 14 days after purchase.")
        self.assertEqual(gated.answer, "Refund eligibility lasts 30 days after purchase.")
        self.assertEqual(gated.citations[0].document_id, "northstar-refund-us-current")

    def test_missing_material_scope_requests_clarification(self):
        result = self.gate.query("refund eligibility window", READER)
        self.assertEqual((result.status, result.answer, result.citations), ("clarify", None, ()))

    def test_us_and_eu_are_distinct_and_cache_scoped(self):
        us = self.gate.query("refund eligibility window", READER, region="us")
        eu = self.gate.query("refund eligibility window", READER, region="eu")
        self.assertFalse(us.cache_hit)
        self.assertFalse(eu.cache_hit)
        self.assertNotEqual(us.answer, eu.answer)

    def test_conflicting_approved_sources_abstain(self):
        result = self.gate.query("enterprise export approval reviewers", READER, region="us")
        self.assertEqual(result.status, "abstain")
        self.assertEqual(result.reason, "conflicting_authoritative_evidence")
        self.assertIsNone(result.answer)

    def test_cross_tenant_cache_cannot_return_other_tenant_answer(self):
        first = self.gate.query("refund eligibility window", READER, region="us")
        other = self.gate.query("refund eligibility window", Principal("harbor"), region="us")
        self.assertFalse(other.cache_hit)
        self.assertNotEqual(first.answer, other.answer)
        self.assertTrue(all(c.source.startswith("fixture://harbor/") for c in other.citations))

    def test_admin_cache_does_not_grant_reader_access(self):
        admin = self.gate.query("privileged access review interval", ADMIN, region="us")
        reader = self.gate.query("privileged access review interval", READER, region="us")
        self.assertEqual(admin.status, "answered")
        self.assertFalse(reader.cache_hit)
        self.assertEqual(reader.status, "abstain")
        self.assertIsNone(reader.answer)

    def test_unknown_tenant_receives_no_evidence_in_either_mode(self):
        for mode in ("baseline", "gated"):
            result = self.gate.query("refund eligibility window", Principal("no-tenant"), region="us", mode=mode)
            self.assertEqual(result.status, "abstain")
            self.assertEqual(result.citations, ())

    def test_same_authorized_context_can_hit_bounded_cache(self):
        gate = EvidenceGate(self.documents, cache_size=1)
        gate.query("refund eligibility window", READER, region="us")
        self.assertTrue(gate.query("refund eligibility window", READER, region="us").cache_hit)
        gate.query("database backup frequency", READER, region="us")
        self.assertFalse(gate.query("refund eligibility window", READER, region="us").cache_hit)

    def test_baseline_cache_does_not_contaminate_gated_answer(self):
        baseline = self.gate.query("refund eligibility window", READER, region="us", mode="baseline")
        gated = self.gate.query("refund eligibility window", READER, region="us")
        self.assertFalse(gated.cache_hit)
        self.assertNotEqual(baseline.answer, gated.answer)

    def test_date_boundary_is_half_open_and_scopes_cache(self):
        before = self.gate.query("refund eligibility window", READER, region="us", as_of="2025-12-31")
        boundary = self.gate.query("refund eligibility window", READER, region="us", as_of="2026-01-01")
        self.assertFalse(boundary.cache_hit)
        self.assertNotEqual(before.answer, boundary.answer)
        self.assertIn("14 days", before.answer)
        self.assertIn("30 days", boundary.answer)

    def test_future_document_is_not_evidence(self):
        future = replace(self.documents[0], id="future", valid_from="2027-01-01")
        gate = EvidenceGate([future])
        self.assertEqual(gate.query("refund eligibility window", READER, region="us").status, "abstain")

    def test_corpus_revision_has_a_different_cache_namespace(self):
        first = EvidenceGate([self.documents[0]])
        modified = replace(self.documents[0], content=self.documents[0].content + " New appendix.")
        second = EvidenceGate([modified])
        self.assertNotEqual(first.version, second.version)

    def test_unknown_question_abstains_without_a_quote(self):
        answer = self.gate.query("orbital launch telemetry", READER, region="us")
        self.assertEqual((answer.status, answer.answer, answer.citations), ("abstain", None, ()))

    def test_deterministic_ties_and_empty_bm25_index(self):
        self.assertEqual(BM25([]).rank("anything"), [])
        doc = self.documents[0]
        ranked = BM25([replace(doc, id="b"), replace(doc, id="a")]).rank("refund")
        self.assertEqual([d.id for d, _, _ in ranked], ["a", "b"])

    def test_query_validation(self):
        for text in ("", " " * 3, "x" * 4097, None):
            with self.assertRaises(ValueError):
                self.gate.query(text, READER, region="us")
        with self.assertRaises(ValueError):
            self.gate.query("refund", READER, region="unknown")

    def test_duplicate_and_nonverbatim_corpus_records_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            write_fixture(folder)
            corpus = folder / "corpus.jsonl"
            original = corpus.read_text(encoding="utf-8")
            corpus.write_text(original + original.splitlines()[0] + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Duplicate"):
                load_documents(corpus)
            first = json.loads(original.splitlines()[0])
            first["claim"] = "Not in content"
            corpus.write_text(json.dumps(first), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "verbatim"):
                load_documents(corpus)

    def test_benchmark_metrics_are_derived_from_percase_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            write_fixture(folder / "data")
            result = run(folder / "data", folder / "results")
            raw = [json.loads(line) for line in (folder / "results/predictions.jsonl").read_text(encoding="utf-8").splitlines()]
            rows = [r for r in raw if r["mode"] == "gated" and r["split"] == "holdout"]
            summary = result["metrics"]["holdout"]["gated"]
            self.assertEqual(summary["cases"], len(rows))
            self.assertEqual(summary["task_successes"], sum(r["task_success"] for r in rows))
            self.assertEqual(summary["answer_attempts"], sum(r["answered"] for r in rows))
            self.assertTrue(all(r["response"]["cache_hit"] is False for r in raw))
            self.assertEqual(len({c["id"] for c in build_cases()}), 32)

    def test_invalid_authorization_metadata_is_rejected(self):
        from dataclasses import asdict
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "corpus.jsonl"
            for change in ({"approved": "false"}, {"roles": "admin"}, {"region": "any"}, {"tenant": 123}):
                path.write_text(json.dumps({**asdict(self.documents[0]), **change}), encoding="utf-8")
                with self.assertRaises(ValueError):
                    load_documents(path)

    def test_report_supporting_links_resolve_from_an_external_output_directory(self):
        project = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "outside-project" / "reports"
            run(project / "data", output)
            text = (output / "case-study.md").read_text(encoding="utf-8")
            expected = {
                "data card": project / "data/DATA_CARD.md",
                "architecture": project / "docs/architecture.md",
                "evaluation protocol": project / "docs/evaluation.md",
            }
            for label, source in expected.items():
                match = re.search(r"\[" + re.escape(label) + r"\]\(([^)]+)\)", text)
                self.assertIsNotNone(match, label)
                target = match.group(1)
                self.assertNotIn("\\", target)
                self.assertEqual((output / target).resolve(), source.resolve())
                self.assertTrue((output / target).exists())
            self.assertTrue((output / "quality.svg").exists())


if __name__ == "__main__":
    unittest.main()
