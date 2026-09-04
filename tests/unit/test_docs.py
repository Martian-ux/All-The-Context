from __future__ import annotations

from pathlib import Path

from scripts.check_docs import broken_links, convergence_ledger_failures


def test_repository_markdown_links_resolve() -> None:
    root = Path(__file__).resolve().parents[2]
    assert broken_links(root) == []


def test_windows_convergence_ledger_shas_are_full_and_resolvable() -> None:
    root = Path(__file__).resolve().parents[2]
    assert convergence_ledger_failures(root) == []
