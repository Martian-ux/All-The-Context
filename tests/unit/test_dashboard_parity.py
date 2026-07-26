from __future__ import annotations

from pathlib import Path

import pytest
from allthecontext.dashboard_parity import compare_asset_trees
from allthecontext.release_manifest import ManifestError


def test_compare_asset_trees_detects_mismatch(tmp_path: Path) -> None:
    committed = tmp_path / "committed"
    built = tmp_path / "built"
    committed.mkdir()
    built.mkdir()
    (committed / "index.html").write_text("a", encoding="utf-8")
    (built / "index.html").write_text("b", encoding="utf-8")
    report = compare_asset_trees(committed, built)
    assert report.ok is False
    assert report.mismatches == ("index.html",)


def test_compare_asset_trees_requires_identical_bytes(tmp_path: Path) -> None:
    committed = tmp_path / "committed"
    built = tmp_path / "built"
    committed.mkdir()
    built.mkdir()
    (committed / "index.html").write_text("same", encoding="utf-8")
    (built / "index.html").write_text("same", encoding="utf-8")
    (committed / "assets" / "app.js").parent.mkdir()
    (built / "assets" / "app.js").parent.mkdir()
    (committed / "assets" / "app.js").write_bytes(b"console.log(1)\n")
    (built / "assets" / "app.js").write_bytes(b"console.log(1)\n")
    report = compare_asset_trees(committed, built)
    assert report.ok is True
    assert report.committed_digest == report.built_digest


def test_missing_built_file_is_reported(tmp_path: Path) -> None:
    committed = tmp_path / "committed"
    built = tmp_path / "built"
    committed.mkdir()
    built.mkdir()
    (committed / "only-committed.txt").write_text("x", encoding="utf-8")
    (built / "only-built.txt").write_text("y", encoding="utf-8")
    report = compare_asset_trees(committed, built)
    assert report.missing_in_built == ("only-committed.txt",)
    assert report.missing_in_committed == ("only-built.txt",)


def test_empty_tree_rejected(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(ManifestError, match="no files"):
        compare_asset_trees(empty, empty)
