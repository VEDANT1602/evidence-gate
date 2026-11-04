"""Original fictional Northstar/Harbor policy corpus, not customer data."""
from dataclasses import asdict
import json
from pathlib import Path
from .core import Document


TOPICS = [
    ("refund", "refund eligibility window", "Refund eligibility lasts 30 days after purchase.", "Refund eligibility lasts 14 days after purchase.", "reader"),
    ("incident", "incident escalation response", "Critical incidents must be escalated within 15 minutes.", "Critical incidents must be escalated within 60 minutes.", "reader"),
    ("retention", "audit log retention duration", "Audit logs are retained for 180 days.", "Audit logs are retained for 90 days.", "reader"),
    ("cache", "retrieval cache expiration ttl", "Retrieval cache entries expire after 300 seconds.", "Retrieval cache entries expire after 900 seconds.", "reader"),
    ("rate", "api request rate limit", "The API request limit is 120 requests per minute.", "The API request limit is 60 requests per minute.", "reader"),
    ("backup", "database backup frequency schedule", "Database backups run every 6 hours.", "Database backups run every 24 hours.", "reader"),
    ("access", "privileged access review interval", "Privileged access is reviewed every 30 days.", "Privileged access is reviewed every 90 days.", "admin"),
    ("recovery", "service recovery objective rto", "The service recovery time objective is 45 minutes.", "The service recovery time objective is 120 minutes.", "reader"),
]


def build_corpus() -> list[Document]:
    documents = []
    for key, words, current, stale, role in TOPICS:
        for region in ("us", "eu"):
            # EU has a distinct supported claim for one policy, so region is a
            # genuinely material facet rather than a cosmetic filter.
            claim = "Refund eligibility lasts 45 days after purchase." if key == "refund" and region == "eu" else current
            for version in ("current", "historical", "draft"):
                selected = claim if version == "current" else stale
                content = f"Northstar {region.upper()} {words}. {selected} "
                if version != "current":
                    # Lexical distractors reproduce the controlled failure:
                    # repetition can outweigh authority in relevance-only rank.
                    content += (words + ". ") * 5
                else:
                    content += "Approved by policy operations. The validity interval governs use."
                documents.append(Document(
                    id=f"northstar-{key}-{region}-{version}", tenant="northstar",
                    title=words if version != "current" else f"Approved {key} standard",
                    content=content, claim_key=key, claim=selected, region=region,
                    valid_from="2026-01-01" if version != "historical" else "2024-01-01",
                    valid_to="2026-01-01" if version == "historical" else None,
                    approved=version != "draft", roles=(role, "admin") if role == "reader" else (role,),
                    source=f"fixture://northstar/{key}/{region}/{version}",
                ))
    for key, words, current, stale, role in TOPICS:
        claim = f"Harbor internal {words}: consult the assigned policy owner."
        documents.append(Document(f"harbor-{key}", "harbor", words, claim, key, claim,
                                  "us", "2026-01-01", None, True, ("reader", "admin"),
                                  f"fixture://harbor/{key}/current"))
    for suffix, claim in (("a", "Enterprise export approval requires one reviewer."),
                          ("b", "Enterprise export approval requires two reviewers.")):
        documents.append(Document(f"northstar-export-{suffix}", "northstar", "enterprise export approval reviewers",
                                  claim, "export", claim, "global", "2026-01-01", None, True,
                                  ("reader", "admin"), f"fixture://northstar/export/{suffix}"))
    return documents


def build_cases() -> list[dict]:
    # Hand-authored prompts and expectations are fixed, not sampled from model output.
    cases = [
        dict(id="dev-refund", split="dev", category="current_policy", query="refund eligibility window", region="us", expected_status="answered", expected_answer=TOPICS[0][2]),
        dict(id="dev-incident", split="dev", category="current_policy", query="incident escalation response", region="us", expected_status="answered", expected_answer=TOPICS[1][2]),
        dict(id="dev-cache", split="dev", category="current_policy", query="retrieval cache expiration ttl", region="us", expected_status="answered", expected_answer=TOPICS[3][2]),
        dict(id="dev-unknown", split="dev", category="unknown", query="quantum settlement procedure", region="us", expected_status="abstain", expected_answer=None),
        dict(id="dev-missing-region", split="dev", category="ambiguous_scope", query="refund eligibility window", region=None, expected_status="clarify", expected_answer=None),
        dict(id="dev-denied", split="dev", category="access_boundary", query="privileged access review interval", region="us", expected_status="abstain", expected_answer=None),
        dict(id="dev-conflict", split="dev", category="conflicting_sources", query="enterprise export approval reviewers", region="us", expected_status="abstain", expected_answer=None),
        dict(id="dev-admin", split="dev", category="current_policy", query="privileged access review interval", region="us", roles=["admin"], expected_status="answered", expected_answer=TOPICS[6][2]),
    ]
    prompts = [
        ("refund", "How long is the refund eligibility window?"),
        ("incident", "What is the incident escalation response?"),
        ("retention", "What is the audit log retention duration?"),
        ("cache", "What is the retrieval cache expiration ttl?"),
        ("rate", "What is the api request rate limit?"),
        ("backup", "What is the database backup frequency schedule?"),
        ("recovery", "What is the service recovery objective rto?"),
    ]
    for key, query in prompts:
        current = next(row[2] for row in TOPICS if row[0] == key)
        for region in ("us", "eu"):
            expected = "Refund eligibility lasts 45 days after purchase." if key == "refund" and region == "eu" else current
            cases.append(dict(id=f"holdout-{key}-{region}", split="holdout", category="current_policy", query=query,
                              region=region, expected_status="answered", expected_answer=expected))
    cases.extend([
        dict(id="holdout-refund-ambiguous", split="holdout", category="ambiguous_scope", query="refund eligibility window", region=None, expected_status="clarify", expected_answer=None),
        dict(id="holdout-backup-ambiguous", split="holdout", category="ambiguous_scope", query="database backup frequency", region=None, expected_status="clarify", expected_answer=None),
        dict(id="holdout-export-conflict", split="holdout", category="conflicting_sources", query="How many enterprise export approval reviewers?", region="eu", expected_status="abstain", expected_answer=None),
        dict(id="holdout-unknown-tax", split="holdout", category="unknown", query="cryptocurrency tax reconciliation", region="us", expected_status="abstain", expected_answer=None),
        dict(id="holdout-unknown-space", split="holdout", category="unknown", query="orbital launch telemetry", region="eu", expected_status="abstain", expected_answer=None),
        dict(id="holdout-denied", split="holdout", category="access_boundary", query="privileged access review interval", region="us", expected_status="abstain", expected_answer=None),
        dict(id="holdout-admin", split="holdout", category="current_policy", query="privileged access review interval", region="eu", roles=["admin"], expected_status="answered", expected_answer=TOPICS[6][2]),
        dict(id="holdout-harbor", split="holdout", category="tenant_isolation", query="refund eligibility window", tenant="harbor", region="us", expected_status="answered", expected_answer="Harbor internal refund eligibility window: consult the assigned policy owner."),
        dict(id="holdout-empty-tenant", split="holdout", category="tenant_isolation", query="refund eligibility window", tenant="empty", region="us", expected_status="abstain", expected_answer=None),
        dict(id="holdout-harbor-cache", split="holdout", category="tenant_isolation", query="retrieval cache expiration ttl", tenant="harbor", region="us", expected_status="answered", expected_answer="Harbor internal retrieval cache expiration ttl: consult the assigned policy owner."),
    ])
    for case in cases:
        case.setdefault("tenant", "northstar")
        case.setdefault("roles", ["reader"])
        case["as_of"] = "2026-11-04"
    return cases


def write_fixture(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    corpus = "".join(json.dumps(asdict(d), sort_keys=True) + "\n" for d in build_corpus())
    (directory / "corpus.jsonl").write_text(corpus, encoding="utf-8")
    (directory / "cases.json").write_text(json.dumps(build_cases(), indent=2) + "\n", encoding="utf-8")
