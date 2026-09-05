from __future__ import annotations

import subprocess
from pathlib import Path

from scripts.check_docs import broken_links, convergence_ledger_failures


def test_repository_markdown_links_resolve() -> None:
    root = Path(__file__).resolve().parents[2]
    assert broken_links(root) == []


def test_windows_convergence_ledger_shas_are_full_and_resolvable() -> None:
    root = Path(__file__).resolve().parents[2]
    assert convergence_ledger_failures(root) == []


def _fresh_git_root(tmp_path: Path, reachable: str, source: str) -> Path:
    root = tmp_path / "fresh-repository"
    ledger = root / "docs" / "integrations"
    ledger.mkdir(parents=True)
    _write_minimal_ledger(ledger / "WINDOWS_GA_CONVERGENCE_20260904.md", reachable, source)
    subprocess.run(["git", "init", "--quiet"], cwd=root, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Docs Test",
            "-c",
            "user.email=docs@example.test",
            "commit",
            "--quiet",
            "--allow-empty",
            "-m",
            "initial",
        ],
        cwd=root,
        check=True,
    )
    return root


def _write_minimal_ledger(path: Path, reachable: str, source: str) -> None:
    path.write_text(
        "\n".join(
            (
                "# Minimal convergence ledger",
                "",
                "## Topological patch ledger",
                "",
                "| # | Local-only source tip (not expected to resolve) | "
                "Reachable integrated commit (ancestor of checked-out HEAD) |",
                "|---:|---|---|",
                f"| 1 | `{source}` | `{reachable}` |",
                "",
            )
        ),
        encoding="utf-8",
    )


def test_declared_local_only_source_tip_passes_without_a_git_object(tmp_path: Path) -> None:
    root = _fresh_git_root(tmp_path, "a" * 40, "b" * 40)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    _write_minimal_ledger(
        root / "docs" / "integrations" / "WINDOWS_GA_CONVERGENCE_20260904.md",
        head,
        "b" * 40,
    )

    assert (
        subprocess.run(
            ["git", "cat-file", "-e", f"{'b' * 40}^{{commit}}"],
            cwd=root,
            check=False,
        ).returncode
        != 0
    )
    assert convergence_ledger_failures(root) == []


def test_local_only_source_provenance_rejects_duplicate_rows(tmp_path: Path) -> None:
    root = _fresh_git_root(tmp_path, "a" * 40, "b" * 40)
    ledger = root / "docs" / "integrations" / "WINDOWS_GA_CONVERGENCE_20260904.md"
    content = ledger.read_text(encoding="utf-8")
    row = f"| 2 | `{'b' * 40}` | `{'a' * 40}` |"
    first_row = f"| 1 | `{'b' * 40}` | `{'a' * 40}` |"
    ledger.write_text(content.replace(first_row, f"{first_row}\n{row}"), encoding="utf-8")

    failures = convergence_ledger_failures(root)

    assert any("source tips are not unique" in failure for failure in failures)


def test_typo_in_reachable_commit_fails_closed(tmp_path: Path) -> None:
    root = _fresh_git_root(tmp_path, "a" * 40, "d" * 40)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    typo = ("b" if head[0] != "b" else "c") + head[1:]
    _write_minimal_ledger(
        root / "docs" / "integrations" / "WINDOWS_GA_CONVERGENCE_20260904.md",
        typo,
        "d" * 40,
    )
    failures = convergence_ledger_failures(root)

    assert any("not an ancestor of checked-out HEAD" in failure for failure in failures)


def test_unclassified_sha_fails_closed(tmp_path: Path) -> None:
    root = _fresh_git_root(tmp_path, "e" * 40, "f" * 40)
    ledger = root / "docs" / "integrations" / "WINDOWS_GA_CONVERGENCE_20260904.md"
    ledger.write_text(
        ledger.read_text(encoding="utf-8") + f"Unclassified token `{('1' * 40)}`.\n",
        encoding="utf-8",
    )

    failures = convergence_ledger_failures(root)

    assert any("no declared class" in failure for failure in failures)


def test_missing_reachable_commit_fails_even_when_local_source_is_declared(tmp_path: Path) -> None:
    root = _fresh_git_root(tmp_path, "0" * 40, "1" * 40)

    failures = convergence_ledger_failures(root)

    assert any("not an ancestor of checked-out HEAD" in failure for failure in failures)
