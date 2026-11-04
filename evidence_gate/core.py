"""Original BM25 retrieval with policy, authorization, and evidence contracts.

This is an extractive reference implementation. It does not call an LLM.
"""
from __future__ import annotations

from collections import Counter, OrderedDict
from dataclasses import asdict, dataclass
from datetime import date
from hashlib import sha256
import json
import math
from pathlib import Path
import re
from threading import RLock


STOP = frozenset("a an and are as at be by can do for from how i in is it me my of on or our please policy should the this to us we what when which with".split())


def tokenize(text: str) -> list[str]:
    # Preserve numbers; a version, limit, or duration is often material evidence.
    return [t for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in STOP]


@dataclass(frozen=True)
class Principal:
    tenant: str
    roles: tuple[str, ...] = ("reader",)


@dataclass(frozen=True)
class Document:
    id: str
    tenant: str
    title: str
    content: str
    claim_key: str
    claim: str
    region: str
    valid_from: str
    valid_to: str | None
    approved: bool
    roles: tuple[str, ...]
    source: str

    def current(self, as_of: date) -> bool:
        return date.fromisoformat(self.valid_from) <= as_of and (
            self.valid_to is None or as_of < date.fromisoformat(self.valid_to)
        )

    def visible(self, principal: Principal) -> bool:
        return self.tenant == principal.tenant and bool(set(self.roles) & set(principal.roles))


@dataclass(frozen=True)
class Citation:
    document_id: str
    source: str
    quote: str
    valid_from: str


@dataclass(frozen=True)
class Answer:
    status: str
    answer: str | None
    reason: str
    citations: tuple[Citation, ...] = ()
    cache_hit: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


class BM25:
    """Okapi BM25 with k1=1.2 and b=0.75, implemented here without copied code."""

    def __init__(self, documents: list[Document]):
        self.documents = documents
        self.terms = [Counter(tokenize(d.title + " " + d.content)) for d in documents]
        self.lengths = [sum(t.values()) for t in self.terms]
        self.average_length = sum(self.lengths) / max(1, len(documents))
        self.df = Counter(token for terms in self.terms for token in terms)

    def rank(self, query: str) -> list[tuple[Document, float, float]]:
        tokens = set(tokenize(query))
        n = len(self.documents)
        result = []
        for doc, terms, length in zip(self.documents, self.terms, self.lengths):
            score = 0.0
            for token in tokens:
                tf = terms[token]
                if not tf:
                    continue
                df = self.df[token]
                idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
                norm = 1.2 * (1 - 0.75 + 0.75 * length / max(1, self.average_length))
                score += idf * tf * 2.2 / (tf + norm)
            coverage = len(tokens & terms.keys()) / max(1, len(tokens))
            if score > 0:
                result.append((doc, score, coverage))
        return sorted(result, key=lambda row: (-row[1], row[0].id))


def load_documents(path: str | Path) -> list[Document]:
    documents = []
    ids = set()
    for line_number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        item = json.loads(line)
        required = set(Document.__dataclass_fields__)
        if not isinstance(item, dict) or set(item) != required:
            raise ValueError(f"Document fields do not match the schema at line {line_number}")
        string_fields = required - {"roles", "approved", "valid_to"}
        if not all(isinstance(item[name], str) and item[name] for name in string_fields):
            raise ValueError(f"Document strings must be nonempty at line {line_number}")
        if type(item["approved"]) is not bool or item["region"] not in {"us", "eu", "global"}:
            raise ValueError(f"Invalid approval or region metadata at line {line_number}")
        if not isinstance(item["roles"], list) or not item["roles"] or not all(isinstance(role, str) and role for role in item["roles"]):
            raise ValueError(f"Roles must be a nonempty string array at line {line_number}")
        if item["valid_to"] is not None and not isinstance(item["valid_to"], str):
            raise ValueError(f"valid_to must be a date string or null at line {line_number}")
        item["roles"] = tuple(item["roles"])
        doc = Document(**item)
        if doc.id in ids:
            raise ValueError(f"Duplicate document id at line {line_number}: {doc.id}")
        if not doc.tenant or not doc.roles or not doc.claim_key or not doc.claim:
            raise ValueError(f"Incomplete document at line {line_number}")
        if doc.claim not in doc.content:
            raise ValueError(f"Claim must be a verbatim span of document content: {doc.id}")
        first = date.fromisoformat(doc.valid_from)
        if doc.valid_to is not None and date.fromisoformat(doc.valid_to) <= first:
            raise ValueError(f"Invalid half-open validity interval: {doc.id}")
        ids.add(doc.id)
        documents.append(doc)
    if not documents:
        raise ValueError("Corpus must contain at least one document")
    return documents


class EvidenceGate:
    """A versioned corpus and an in-memory, bounded, principal-scoped result cache.

    The caller must establish a trusted Principal. See the authenticated local
    HTTP adapter; user-supplied tenant/role fields are not an auth mechanism.
    """

    def __init__(self, documents: list[Document], *, cache_size: int = 128, minimum_coverage: float = 0.30):
        if cache_size < 0:
            raise ValueError("cache_size must be non-negative")
        if not 0 < minimum_coverage <= 1:
            raise ValueError("minimum_coverage must be in (0, 1]")
        self.documents = tuple(documents)
        self.version = sha256(json.dumps([asdict(d) for d in self.documents], sort_keys=True).encode()).hexdigest()
        self.cache_size = cache_size
        self.minimum_coverage = minimum_coverage
        self._cache: OrderedDict[tuple, Answer] = OrderedDict()
        self._lock = RLock()

    def query(self, text: str, principal: Principal, *, region: str | None = None,
              as_of: str = "2026-11-04", mode: str = "gated") -> Answer:
        if mode not in {"gated", "baseline"}:
            raise ValueError("mode must be gated or baseline")
        if not isinstance(text, str) or not text.strip() or len(text) > 4096:
            raise ValueError("query text must contain 1-4096 characters")
        if not principal.tenant or not principal.roles:
            raise ValueError("trusted principal requires tenant and at least one role")
        day = date.fromisoformat(as_of)
        if region is not None and region not in {"us", "eu"}:
            raise ValueError("region must be us, eu, or omitted")
        key = (self.version, principal.tenant, tuple(sorted(set(principal.roles))), region,
               day.isoformat(), mode, self.minimum_coverage, text.strip().lower())
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                old = self._cache[key]
                return Answer(old.status, old.answer, old.reason, old.citations, True)

        # Both comparators enforce ACLs before scoring. Safety improvement is not
        # obtained by deliberately giving the baseline unauthorized documents.
        visible = [d for d in self.documents if d.visible(principal)]
        if mode == "gated":
            visible = [d for d in visible if d.approved and d.current(day)]
            if region is not None:
                visible = [d for d in visible if d.region in {"global", region}]
        ranked = BM25(visible).rank(text)
        if not ranked:
            answer = Answer("abstain", None, "no_supported_evidence")
        elif mode == "baseline":
            answer = self._extract(ranked[0][0])
        elif ranked[0][2] < self.minimum_coverage:
            answer = Answer("abstain", None, "insufficient_query_coverage")
        else:
            top = ranked[0][0]
            # A managed claim key links versions/scopes; free-form conflict
            # detection would require a different and separately evaluated model.
            claim_documents = [d for d in visible if d.claim_key == top.claim_key]
            scoped_regions = {d.region for d in claim_documents if d.region != "global"}
            if region is None and len(scoped_regions) > 1:
                answer = Answer("clarify", None, "region_required")
            elif len({d.claim for d in claim_documents}) > 1:
                answer = Answer("abstain", None, "conflicting_authoritative_evidence")
            else:
                answer = self._extract(top)
        with self._lock:
            if self.cache_size:
                self._cache[key] = answer
                while len(self._cache) > self.cache_size:
                    self._cache.popitem(last=False)
        return answer

    @staticmethod
    def _extract(doc: Document) -> Answer:
        return Answer("answered", doc.claim, "verbatim_evidence",
                      (Citation(doc.id, doc.source, doc.claim, doc.valid_from),))
