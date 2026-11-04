from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys

from .core import EvidenceGate, Principal, load_documents

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA = ROOT / "data" if (ROOT / "data/corpus.jsonl").exists() else Path(sys.prefix) / "share/evidence-gate"


def main():
    parser = argparse.ArgumentParser(description="Evidence Gate: inspectable extractive evidence contracts")
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("fixture", help="Regenerate the original fictional corpus and fixed cases")
    benchmark = commands.add_parser("benchmark", help="Write measured metrics, raw predictions, report and chart")
    benchmark.add_argument("--output", type=Path, default=Path.cwd() / "results")
    query = commands.add_parser("query", help="In-process demo: caller establishes a trusted principal")
    query.add_argument("text")
    query.add_argument("--tenant", default="northstar")
    query.add_argument("--role", action="append", dest="roles")
    query.add_argument("--region", choices=("us", "eu"))
    query.add_argument("--as-of", default="2026-11-04")
    query.add_argument("--mode", choices=("baseline", "gated"), default="gated")
    server = commands.add_parser("serve", help="Authenticated loopback-only HTTP demo")
    server.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    if args.command == "fixture":
        from .fixture import write_fixture
        write_fixture(args.data)
        print(f"Wrote original fictional fixture to {args.data}")
    elif args.command == "benchmark":
        from .benchmark import run
        result = run(args.data, args.output)
        print(json.dumps(result["metrics"], indent=2, sort_keys=True))
    else:
        gate = EvidenceGate(load_documents(args.data / "corpus.jsonl"))
        if args.command == "query":
            print(json.dumps(gate.query(args.text, Principal(args.tenant, tuple(args.roles or ["reader"])),
                                       region=args.region, as_of=args.as_of, mode=args.mode).to_dict(), indent=2))
        else:
            from .api import serve
            serve(gate, args.port)


if __name__ == "__main__":
    main()
