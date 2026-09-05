"""Fail when a relative Markdown link points at a missing repository file."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from typing import cast
from urllib.parse import unquote

LINK = re.compile(r"(?<!!)\[[^]]*]\(([^)]+)\)")
CODE_TOKEN = re.compile(r"`([^`\r\n]+)`")
HEX_TOKEN = re.compile(r"[0-9a-fA-F]+")
CONVERGENCE_LEDGER = Path("docs/integrations/WINDOWS_GA_CONVERGENCE_20260904.md")
LOCAL_SOURCE_TIP = "local_source_tip"
REACHABLE_COMMIT = "reachable_commit"


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
    """Validate declared commit evidence in the convergence ledger.

    Integrated commits are repository evidence and must be ancestors of the
    checked-out ``HEAD``. Original worker source tips are deliberately a
    separate, local-only provenance class: their identity cannot be resolved
    from a portable clone, so the ledger structure validates their narrower
    format, uniqueness, count, and source-to-integration lineage contract.
    """

    ledger = root / CONVERGENCE_LEDGER
    if not ledger.exists():
        return [f"missing convergence ledger: {CONVERGENCE_LEDGER}"]

    text = ledger.read_text(encoding="utf-8")
    failures: list[str] = []

    declarations: list[tuple[str, str | None, int]] = []
    tables: list[dict[str, object]] = []
    current_table: dict[str, object] | None = None
    section = ""
    lines = text.splitlines()

    for line_number, line in enumerate(lines, start=1):
        if line.startswith("## "):
            section = line[3:].strip()
            current_table = None

        cells = _table_cells(line)
        if cells is not None and line_number < len(lines):
            next_cells = _table_cells(lines[line_number])
            if next_cells is not None and _is_table_separator(next_cells):
                column_kinds = {
                    index: kind
                    for index, cell in enumerate(cells)
                    if (kind := _column_kind(cell)) is not None
                }
                current_table = {
                    "section": section,
                    "column_kinds": column_kinds,
                    "rows": [],
                }
                if column_kinds:
                    tables.append(current_table)
                continue

        if cells is not None and current_table is not None:
            column_kinds = cast(dict[int, str], current_table["column_kinds"])
            if not isinstance(column_kinds, dict):
                current_table = None
            elif _is_table_separator(cells):
                continue
            else:
                row: dict[str, object] = {
                    "line": line_number,
                    "number": cells[0] if cells else "",
                    LOCAL_SOURCE_TIP: [],
                    REACHABLE_COMMIT: [],
                }
                rows = current_table["rows"]
                if isinstance(rows, list):
                    rows.append(row)
                for cell_index, cell in enumerate(cells):
                    for match in CODE_TOKEN.finditer(cell):
                        token = match.group(1)
                        if not _looks_like_commit_token(token):
                            continue
                        kind = column_kinds.get(cell_index)
                        if not column_kinds:
                            kind = _line_kind(line)
                        _record_token(
                            declarations,
                            row,
                            token,
                            kind,
                            line_number,
                        )
                continue

        if cells is None:
            current_table = None
        line_kind = _line_kind(line)
        for match in CODE_TOKEN.finditer(line):
            token = match.group(1)
            if _looks_like_commit_token(token):
                _record_token(declarations, None, token, line_kind, line_number)

    for token, kind, line_number in declarations:
        if kind is None:
            failures.append(f"{ledger}:{line_number}: commit token has no declared class: {token}")
            continue
        if not re.fullmatch(r"[0-9a-f]{40}", token):
            failures.append(
                f"{ledger}:{line_number}: {kind} must be lowercase 40 hex characters: {token}"
            )

    failures.extend(_validate_ledger_tables(ledger, tables))

    reachable_tokens = {token for token, kind, _ in declarations if kind == REACHABLE_COMMIT}
    head_ready = _git_commit_exists(root, "HEAD")
    if not head_ready and reachable_tokens:
        failures.append(f"{ledger}: checked-out HEAD is not a commit")
    elif head_ready:
        for token in sorted(reachable_tokens):
            if re.fullmatch(r"[0-9a-f]{40}", token) and not _git_commit_reachable(root, token):
                failures.append(
                    f"{ledger}: reachable commit is not an ancestor of checked-out HEAD: {token}"
                )
    return failures


def _table_cells(line: str) -> list[str] | None:
    stripped = line.strip()
    if not stripped.startswith("|") or not stripped.endswith("|"):
        return None
    return [cell.strip() for cell in stripped[1:-1].split("|")]


def _is_table_separator(cells: list[str]) -> bool:
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells)


def _column_kind(header: str) -> str | None:
    normalized = " ".join(header.casefold().split())
    if "local-only source tip" in normalized:
        return LOCAL_SOURCE_TIP
    if "reachable integrated commit" in normalized:
        return REACHABLE_COMMIT
    return None


def _line_kind(line: str) -> str | None:
    normalized = line.casefold()
    if "local-only source tip" in normalized or "not expected to resolve" in normalized:
        return LOCAL_SOURCE_TIP
    if "reachable commit" in normalized or "checked-out history" in normalized:
        return REACHABLE_COMMIT
    return None


def _looks_like_commit_token(token: str) -> bool:
    return len(token) >= 20 and bool(HEX_TOKEN.fullmatch(token))


def _record_token(
    declarations: list[tuple[str, str | None, int]],
    row: dict[str, object] | None,
    token: str,
    kind: str | None,
    line_number: int,
) -> None:
    declarations.append((token, kind, line_number))
    if row is not None and kind in {LOCAL_SOURCE_TIP, REACHABLE_COMMIT}:
        values = row[kind]
        if isinstance(values, list):
            values.append(token)


def _validate_ledger_tables(ledger: Path, tables: list[dict[str, object]]) -> list[str]:
    failures: list[str] = []
    required_source_values: set[str] | None = None
    patch_source_values: set[str] | None = None

    for table in tables:
        rows = table["rows"]
        column_kinds = table["column_kinds"]
        section = str(table["section"])
        if not isinstance(rows, list) or not isinstance(column_kinds, dict):
            continue
        if not rows:
            failures.append(f"{ledger}: declared ledger table has no data rows: {section}")
            continue

        expected_number = 1
        local_values: list[str] = []
        reachable_values: list[str] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            line_number = row["line"]
            number = row["number"]
            if number != str(expected_number):
                failures.append(
                    f"{ledger}:{line_number}: ledger row number must {expected_number}, "
                    f"got {number}"
                )
            expected_number += 1
            local = row[LOCAL_SOURCE_TIP]
            reachable = row[REACHABLE_COMMIT]
            if LOCAL_SOURCE_TIP in column_kinds.values():
                if not isinstance(local, list) or len(local) != 1:
                    failures.append(
                        f"{ledger}:{line_number}: each local-only source row requires "
                        "one source tip"
                    )
                elif isinstance(local, list):
                    local_values.extend(local)
            if REACHABLE_COMMIT in column_kinds.values():
                if not isinstance(reachable, list) or len(reachable) != 1:
                    failures.append(
                        f"{ledger}:{line_number}: each integration row requires "
                        "one reachable commit"
                    )
                elif isinstance(reachable, list):
                    reachable_values.extend(reachable)

        if len(local_values) != len(set(local_values)):
            failures.append(f"{ledger}: local-only source tips are not unique within {section}")
        if len(reachable_values) != len(set(reachable_values)):
            failures.append(
                f"{ledger}: reachable integrated commits are not unique within {section}"
            )

        section_normalized = section.casefold()
        if "required source tips" in section_normalized:
            required_source_values = set(local_values)
        if "topological patch ledger" in section_normalized:
            patch_source_values = set(local_values)

    if required_source_values is not None:
        if patch_source_values is None:
            failures.append(f"{ledger}: required source tips have no patch ledger")
        else:
            missing = sorted(required_source_values - patch_source_values)
            if missing:
                failures.append(
                    f"{ledger}: required source tips missing from patch ledger: "
                    f"{', '.join(missing)}"
                )
    return failures


def _git_commit_exists(root: Path, revision: str) -> bool:
    resolved = subprocess.run(
        ["git", "rev-parse", "--verify", f"{revision}^{{commit}}"],
        cwd=root,
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return resolved.returncode == 0


def _git_commit_reachable(root: Path, token: str) -> bool:
    if not _git_commit_exists(root, token):
        return False
    resolved = subprocess.run(
        ["git", "merge-base", "--is-ancestor", token, "HEAD"],
        cwd=root,
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return resolved.returncode == 0


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    failures = [*broken_links(root), *convergence_ledger_failures(root)]
    for failure in failures:
        print(failure)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
