"""Fail when a relative Markdown link points at a missing repository file."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import unquote

LINK = re.compile(r"(?<!!)\[[^]]*]\(([^)]+)\)")


def broken_links(root: Path) -> list[str]:
    failures: list[str] = []
    for document in sorted(root.rglob("*.md"), key=lambda path: str(path).casefold()):
        relative_parts = document.relative_to(root).parts
        if any(part in {".git", ".venv", "node_modules"} for part in relative_parts):
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


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    failures = broken_links(root)
    for failure in failures:
        print(failure)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
