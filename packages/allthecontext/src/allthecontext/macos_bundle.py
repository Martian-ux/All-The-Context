"""Safety checks for links confined to a native macOS application bundle."""

from __future__ import annotations

import hashlib
import os
from collections.abc import Iterator
from pathlib import Path


class MacOSBundleError(RuntimeError):
    """A macOS bundle link is unsafe or structurally invalid."""


def _bundle_entries(bundle: Path) -> Iterator[Path]:
    for directory, directories, files in os.walk(bundle, followlinks=False):
        root = Path(directory)
        for name in (*directories, *files):
            yield root / name


def _same_file(left: Path, right: Path) -> bool:
    try:
        return left.samefile(right)
    except OSError:
        return False


def _is_within_bundle(bundle: Path, target: Path) -> bool:
    # samefile follows the host filesystem's actual case/identity rules. This
    # avoids assuming either a case-sensitive or case-insensitive macOS volume.
    return any(_same_file(candidate, bundle) for candidate in (target, *target.parents))


def _is_directory_cycle(bundle: Path, link: Path, target: Path) -> bool:
    if not target.is_dir():
        return False
    for ancestor in (link.parent, *link.parent.parents):
        if _same_file(target, ancestor):
            return True
        if _same_file(ancestor, bundle):
            return False
    return False


def _raw_target_stays_within_bundle(bundle: Path, link: Path, target: Path) -> bool:
    if target.drive or target.root:
        return False
    try:
        depth = len(link.parent.relative_to(bundle).parts)
    except ValueError:
        return False
    for component in target.parts:
        if component in {"", "."}:
            continue
        if component == "..":
            depth -= 1
            if depth < 0:
                return False
        else:
            depth += 1
    return True


def validate_macos_bundle_link(bundle: Path, link: Path, target_value: Path) -> None:
    """Validate one raw link target without trusting lexical path comparison."""

    if target_value.is_absolute():
        raise MacOSBundleError("macOS bundle contains an absolute link")
    if not _raw_target_stays_within_bundle(bundle, link, target_value):
        raise MacOSBundleError("macOS bundle link leaves the bundle before resolution")
    try:
        target = (link.parent / target_value).resolve(strict=True)
    except (FileNotFoundError, OSError, RuntimeError) as exc:
        raise MacOSBundleError("macOS bundle contains a dangling or cyclic link") from exc
    if not _is_within_bundle(bundle, target):
        raise MacOSBundleError("macOS bundle link escapes the application bundle")
    if _is_directory_cycle(bundle, link, target):
        raise MacOSBundleError("macOS bundle contains a directory-link cycle")


def validate_macos_bundle_links(bundle: Path) -> None:
    """Reject unsafe links before preserving the native app bundle structure."""

    root = bundle.expanduser().resolve(strict=True)
    if not root.is_dir():
        raise MacOSBundleError("macOS application bundle is missing or invalid")
    casefolded_paths: dict[str, str] = {}
    for entry in _bundle_entries(root):
        relative = entry.relative_to(root).as_posix()
        folded = relative.casefold()
        previous = casefolded_paths.setdefault(folded, relative)
        if previous != relative:
            raise MacOSBundleError("macOS bundle contains case-colliding paths")
        if entry.is_symlink():
            validate_macos_bundle_link(root, entry, entry.readlink())
        elif entry.is_mount():
            raise MacOSBundleError("macOS bundle contains a nested filesystem mount")
        elif not entry.is_dir() and not entry.is_file():
            raise MacOSBundleError("macOS bundle contains an unsupported filesystem entry")


def macos_bundle_fingerprint(bundle: Path) -> str:
    """Hash every bundle entry without depending on timestamps or POSIX modes."""

    root = bundle.expanduser().resolve(strict=True)
    validate_macos_bundle_links(root)
    digest = hashlib.sha256()
    entries = sorted(
        _bundle_entries(root),
        key=lambda entry: entry.relative_to(root).as_posix().casefold(),
    )
    for entry in entries:
        relative = entry.relative_to(root).as_posix().encode("utf-8")
        if entry.is_symlink():
            digest.update(b"link\0" + relative + b"\0")
            digest.update(os.fsencode(entry.readlink()))
        elif entry.is_dir():
            digest.update(b"directory\0" + relative + b"\0")
        elif entry.is_file():
            digest.update(b"file\0" + relative + b"\0")
            with entry.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
        else:
            raise MacOSBundleError("macOS bundle contains an unsupported filesystem entry")
    return digest.hexdigest()
