"""Fail when a relative Markdown link points at a missing repository file."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote

LINK = re.compile(r"(?<!!)\[[^]]*]\(([^)]+)\)")
COMMIT_TOKEN = re.compile(r"`([0-9a-f]+)`")
CONVERGENCE_LEDGER = Path("docs/integrations/WINDOWS_GA_CONVERGENCE_20260904.md")


def broken_links(root: Path) -> list[str]:
    failures: list[str] = []
    for document in sorted(root.rglob("*.md"), key=lambda path: str(path).casefold()):
        relative_parts = document.relative_to(root).parts
        if any(
            part in {".git", "build", "dist", "node_modules", "tmp"}
            or part.startswith((".tmp", ".venv"))
            for part in relative_parts
        ):
            continue
        text = document.read_text(encoding="utf-8")
        for match in LINK.finditer(text):
            target = match.group(1).strip().strip("<>").split("#", 1)[0]
            if not target or "://" in target or target.startswith("mailto:"):
                continue
            resolved = (document.parent / unquote(target)).resolve()
            try:
                resolved.relative_to(root.resolve())
            except ValueError:
                failures.append(f"{document}: link leaves repository: {target}")
                continue
            if not resolved.exists():
                failures.append(f"{document}: missing target: {target}")
    return failures


def convergence_ledger_failures(root: Path) -> list[str]:
    """Require every long hexadecimal ledger token to be a local commit SHA."""

    ledger = root / CONVERGENCE_LEDGER
    if not ledger.exists():
        return [f"missing convergence ledger: {CONVERGENCE_LEDGER}"]
    failures: list[str] = []
    tokens = {
        match.group(1)
        for match in COMMIT_TOKEN.finditer(ledger.read_text(encoding="utf-8"))
        if len(match.group(1)) >= 20
    }
    for token in sorted(tokens):
        if len(token) != 40:
            failures.append(f"{ledger}: commit SHA is not 40 hex characters: {token}")
            continue
        resolved = subprocess.run(
            ["git", "cat-file", "-e", f"{token}^{{commit}}"],
            cwd=root,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if resolved.returncode != 0:
            failures.append(f"{ledger}: commit SHA does not resolve: {token}")
    return failures


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    failures = [*broken_links(root), *convergence_ledger_failures(root)]
    for failure in failures:
        print(failure)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
