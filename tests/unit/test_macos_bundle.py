from __future__ import annotations

from pathlib import Path

import pytest
from allthecontext.macos_bundle import (
    MacOSBundleError,
    macos_bundle_fingerprint,
    validate_macos_bundle_link,
    validate_macos_bundle_links,
)


def _bundle_layout(tmp_path: Path) -> tuple[Path, Path]:
    bundle = tmp_path / "All The Context.app"
    link = bundle / "Contents" / "Frameworks" / "Current"
    target = bundle / "Contents" / "Resources" / "payload.bin"
    link.parent.mkdir(parents=True)
    target.parent.mkdir(parents=True)
    target.write_bytes(b"bundle payload")
    return bundle.resolve(), link


def test_relative_bundle_link_must_resolve_inside_bundle(tmp_path: Path) -> None:
    bundle, link = _bundle_layout(tmp_path)

    validate_macos_bundle_link(bundle, link, Path("../Resources/payload.bin"))


def test_absolute_bundle_link_is_rejected(tmp_path: Path) -> None:
    bundle, link = _bundle_layout(tmp_path)
    outside = (tmp_path / "outside").resolve()
    outside.write_bytes(b"outside")

    with pytest.raises(MacOSBundleError, match="absolute"):
        validate_macos_bundle_link(bundle, link, outside)


def test_bundle_escaping_link_is_rejected_by_filesystem_identity(tmp_path: Path) -> None:
    bundle, link = _bundle_layout(tmp_path)
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"outside")

    with pytest.raises(MacOSBundleError, match=r"leaves|escapes"):
        validate_macos_bundle_link(bundle, link, Path("../../../outside.bin"))


def test_escape_then_external_reentry_shape_is_rejected_before_resolution(tmp_path: Path) -> None:
    bundle, link = _bundle_layout(tmp_path)

    with pytest.raises(MacOSBundleError, match="before resolution"):
        validate_macos_bundle_link(bundle, link, Path("../../../outside/back/payload.bin"))


def test_dangling_bundle_link_is_rejected(tmp_path: Path) -> None:
    bundle, link = _bundle_layout(tmp_path)

    with pytest.raises(MacOSBundleError, match="dangling or cyclic"):
        validate_macos_bundle_link(bundle, link, Path("missing-target"))


def test_directory_link_to_ancestor_is_rejected_as_cycle(tmp_path: Path) -> None:
    bundle, _link = _bundle_layout(tmp_path)
    loop_directory = bundle / "Contents" / "Loop"
    loop_directory.mkdir()
    link = loop_directory / "Ancestor"

    with pytest.raises(MacOSBundleError, match="directory-link cycle"):
        validate_macos_bundle_link(bundle, link, Path(".."))


def test_bundle_fingerprint_covers_external_resources(tmp_path: Path) -> None:
    bundle, _link = _bundle_layout(tmp_path)
    before = macos_bundle_fingerprint(bundle)
    resource = bundle / "Contents" / "Resources" / "payload.bin"

    resource.write_bytes(b"changed resource without changing executable")

    assert macos_bundle_fingerprint(bundle) != before


def test_case_colliding_bundle_paths_are_rejected(tmp_path: Path, monkeypatch) -> None:
    bundle, _link = _bundle_layout(tmp_path)
    upper = bundle / "Contents" / "Resources" / "Name.txt"
    lower = bundle / "Contents" / "Resources" / "name.txt"
    upper.write_text("upper", encoding="utf-8")
    lower.write_text("lower", encoding="utf-8")
    monkeypatch.setattr(
        "allthecontext.macos_bundle._bundle_entries",
        lambda _bundle: iter((upper, lower)),
    )

    with pytest.raises(MacOSBundleError, match="case-colliding"):
        validate_macos_bundle_links(bundle)
