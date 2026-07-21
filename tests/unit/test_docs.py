from __future__ import annotations

from pathlib import Path

from scripts.check_docs import broken_links


def test_repository_markdown_links_resolve() -> None:
    root = Path(__file__).resolve().parents[2]
    assert broken_links(root) == []
