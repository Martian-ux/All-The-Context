"""Install Python dependencies from the reviewed uv.lock rather than broad ranges."""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
# Reviewed local baseline; bootstrap and workflows must use this exact uv.
PINNED_UV_VERSION = "0.11.32"
UV_VERSION_PATTERN = re.compile(r"\b(\d+\.\d+\.\d+)\b")
BUILD_BACKEND_PACKAGES = ("setuptools", "wheel")


def _run(command: list[str], *, cwd: Path | None = None) -> None:
    subprocess.run(command, cwd=cwd, check=True)


def _uv_version(uv_path: str) -> str | None:
    completed = subprocess.run(
        [uv_path, "--version"],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        return None
    match = UV_VERSION_PATTERN.search(completed.stdout or completed.stderr or "")
    return match.group(1) if match else None


def ensure_pinned_uv(python: str) -> str:
    """Return a uv executable at PINNED_UV_VERSION, installing only that pin when needed."""

    candidates: list[str] = []
    which = shutil.which("uv")
    if which is not None:
        candidates.append(which)
    sibling = str(Path(python).with_name("uv.exe" if sys.platform == "win32" else "uv"))
    if sibling not in candidates:
        candidates.append(sibling)
    for candidate in candidates:
        if not Path(candidate).is_file() and shutil.which(candidate) is None:
            continue
        version = _uv_version(candidate)
        if version == PINNED_UV_VERSION:
            return candidate
    _run([python, "-m", "pip", "install", "--upgrade", f"uv=={PINNED_UV_VERSION}"])
    installed = shutil.which("uv") or str(
        Path(python).with_name("uv.exe" if sys.platform == "win32" else "uv")
    )
    version = _uv_version(installed)
    if version != PINNED_UV_VERSION:
        raise RuntimeError(
            f"uv bootstrap did not yield pinned {PINNED_UV_VERSION} (found {version!r})"
        )
    return installed


def _hash_requirement_from_lock(lock: dict[str, object], name: str) -> str | None:
    packages = lock.get("package")
    if not isinstance(packages, list):
        return None
    for package in packages:
        if not isinstance(package, dict) or package.get("name") != name:
            continue
        version = package.get("version")
        if not isinstance(version, str):
            return None
        hashes: list[str] = []
        for key in ("wheels",):
            entries = package.get(key)
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                digest = entry.get("hash")
                if isinstance(digest, str) and digest.startswith("sha256:"):
                    hashes.append(digest.removeprefix("sha256:"))
        sdist = package.get("sdist")
        if isinstance(sdist, dict):
            digest = sdist.get("hash")
            if isinstance(digest, str) and digest.startswith("sha256:"):
                hashes.append(digest.removeprefix("sha256:"))
        if not hashes:
            return None
        hash_flags = " ".join(f"--hash=sha256:{item}" for item in hashes)
        return f"{name}=={version} {hash_flags}"
    return None


def _install_locked_build_backends(python: str, root: Path, temporary: Path) -> None:
    """Install setuptools/wheel from uv.lock digests for --no-build-isolation."""

    lock = tomllib.loads((root / "uv.lock").read_text(encoding="utf-8"))
    lines: list[str] = []
    for name in BUILD_BACKEND_PACKAGES:
        requirement = _hash_requirement_from_lock(lock, name)
        if requirement is not None:
            lines.append(requirement)
    if not lines:
        # setuptools is required for the setuptools.build_meta backend.
        raise RuntimeError("uv.lock is missing hashed setuptools for locked builds")
    requirements = temporary / "build-backends.txt"
    requirements.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _run(
        [
            python,
            "-m",
            "pip",
            "install",
            "--require-hashes",
            "-r",
            str(requirements),
        ],
        cwd=root,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", type=Path, default=REPOSITORY_ROOT)
    parser.add_argument(
        "--extra",
        action="append",
        default=[],
        help="optional project extras to install (for example dev or packaging)",
    )
    parser.add_argument(
        "--skip-project",
        action="store_true",
        help="install only locked third-party constraints without the local project",
    )
    arguments = parser.parse_args()
    root = arguments.repository_root.resolve()
    lock = root / "uv.lock"
    if not lock.is_file():
        print("install_locked_python error: uv.lock is missing", file=sys.stderr)
        return 1
    python = sys.executable
    try:
        uv = ensure_pinned_uv(python)
        with tempfile.TemporaryDirectory(prefix="atc-locked-") as temporary_name:
            temporary = Path(temporary_name)
            constraints = temporary / "constraints.txt"
            # Export frozen versions WITH hashes so pip can enforce digests.
            export_command = [
                uv,
                "export",
                "--frozen",
                "--no-emit-project",
                "--output-file",
                str(constraints),
            ]
            if not arguments.extra:
                export_command.append("--no-dev")
            for extra in arguments.extra:
                export_command.extend(["--extra", extra])
            subprocess.run(
                export_command,
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
            )
            constraints_text = constraints.read_text(encoding="utf-8")
            if "--hash=" not in constraints_text and "sha256:" not in constraints_text:
                raise RuntimeError("uv export did not produce hash-pinned requirements")
            # Third-party packages only, hash-enforced.
            _run(
                [
                    python,
                    "-m",
                    "pip",
                    "install",
                    "--require-hashes",
                    "-r",
                    str(constraints),
                ],
                cwd=root,
            )
            if not arguments.skip_project:
                # Build backend must come from the reviewed lock digests, not an
                # unpinned download during uncontrolled build isolation.
                _install_locked_build_backends(python, root, temporary)
                extras = ""
                if arguments.extra:
                    extras = "[" + ",".join(arguments.extra) + "]"
                # Local project: exact checkout, no dependency re-resolution, no
                # uncontrolled build isolation (build tools come from the lock).
                _run(
                    [
                        python,
                        "-m",
                        "pip",
                        "install",
                        "--no-deps",
                        "--no-build-isolation",
                        "-e",
                        f".{extras}",
                    ],
                    cwd=root,
                )
        print(
            f"installed Python dependencies from reviewed uv.lock "
            f"(uv=={PINNED_UV_VERSION}, require-hashes)"
        )
        return 0
    except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
        print(f"install_locked_python error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
