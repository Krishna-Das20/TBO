"""hawk CLI.

  hawk scan [--target voyage] [--approve-all | --interactive]
  hawk findings
  hawk llm-status
"""
from __future__ import annotations

import argparse
import asyncio
import json

from .executors import Test
from .llm import get_llm
from .loop import run_scan
from .store import Store


def main() -> None:
    p = argparse.ArgumentParser(prog="hawk")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan")
    s.add_argument("--target", default="voyage")
    g = s.add_mutually_exclusive_group()
    g.add_argument("--approve-all", action="store_true", help="approve money-movement tests (sandbox only)")
    g.add_argument("--interactive", action="store_true", help="ask before each test that needs approval")
    sub.add_parser("findings")
    sub.add_parser("llm-status")
    a = p.parse_args()

    if a.cmd == "scan":
        async def approve(t: Test) -> bool:
            if a.approve_all:
                return True
            if a.interactive:
                return input(f"Approve {t.executor} on {t.endpoint} ({t.risk})? [y/N] ").strip().lower() == "y"
            return False
        result = asyncio.run(run_scan(a.target, approve=approve))
        print(json.dumps(result, indent=2))
    elif a.cmd == "findings":
        for f in Store().findings():
            print(f"{f.id}  {f.status:6} {f.severity:8} {f.score:4}  {f.owner:28} {f.title}")
    elif a.cmd == "llm-status":
        print(json.dumps(get_llm().status(), indent=2))


if __name__ == "__main__":
    main()
