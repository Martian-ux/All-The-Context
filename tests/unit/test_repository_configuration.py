from __future__ import annotations

from pathlib import Path

import allthecontext


def test_pytest_imports_this_checkout_source_tree() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    expected_source_root = (repository_root / "packages" / "allthecontext" / "src").resolve()
    imported_path = Path(allthecontext.__file__).resolve()

    assert imported_path.is_relative_to(expected_source_root)
