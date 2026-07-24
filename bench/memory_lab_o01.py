"""Run the isolated deterministic O01 Memory Lab experiment."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from allthecontext.memory_lab_o01 import run_o01_file

FIXTURE = Path(__file__).with_name("memory_lab_o01_fixture.json")
DEFAULT_REPORT = Path(__file__).with_name("reports") / "memory_lab_o01.json"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", type=Path, default=FIXTURE)
    parser.add_argument("--output", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()
    report = run_o01_file(args.fixture)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report["decision"], sort_keys=True))


if __name__ == "__main__":
    main()
