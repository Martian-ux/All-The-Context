"""Install Python dependencies from the reviewed uv.lock rather than broad ranges."""

from __future__ import annotations

import argparse
import importlib.metadata
import importlib.util
import os
import platform
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
# Complete locked dependency closure for the no-build-isolation environment.
# wheel 0.47.0 requires packaging>=24, so packaging must be hash-pinned here too.
BUILD_BACKEND_PACKAGES = ("packaging", "setuptools", "wheel")


def _run(
    command: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> None:
    subprocess.run(command, cwd=cwd, check=True, env=env)


def locked_install_environment(
    extras: list[str],
    *,
    system: str | None = None,
    environ: dict[str, str] | None = None,
) -> dict[str, str]:
    """Return the reviewed third-party install environment.

    Cryptography 50 does not publish a macOS x86-64 wheel. Its source build
    otherwise links Homebrew OpenSSL dynamically, while the frozen application
    also collects Python's same-basename OpenSSL libraries. Static linkage keeps
    the cryptography extension self-contained and removes that ambiguous bundle
    collision. Other installs retain their inherited environment unchanged.
    """

    active_system = system or platform.system()
    result = dict(os.environ if environ is None else environ)
    if active_system == "Darwin" and "packaging" in extras:
        result["OPENSSL_STATIC"] = "1"
        # A cached wheel may have been built dynamically before this policy
        # existed. Force this hash-verified install to rebuild the missing
        # Intel wheel under the static-link environment.
        result["PIP_NO_CACHE_DIR"] = "1"
    return result


def _macos_cryptography_openssl_dependencies(output: str) -> set[str]:
    """Return dynamic OpenSSL library basenames reported by ``otool -L``."""

    expected = {"libcrypto.3.dylib", "libssl.3.dylib"}
    return {
        Path(line.strip().split(" (compatibility version", maxsplit=1)[0]).name
        for line in output.splitlines()[1:]
        if line.strip()
    } & expected


def verify_static_macos_cryptography(
    extras: list[str],
    *,
    system: str | None = None,
) -> None:
    """Fail closed if a macOS packaging install retained dynamic OpenSSL."""

    active_system = system or platform.system()
    if active_system != "Darwin" or "packaging" not in extras:
        return
    binding = importlib.util.find_spec("cryptography.hazmat.bindings._rust")
    if binding is None or binding.origin is None:
        raise RuntimeError("cryptography Rust binding is unavailable after locked install")
    otool = shutil.which("otool")
    if otool is None:
        raise RuntimeError("otool is required to verify macOS cryptography linkage")
    completed = subprocess.run(
        [otool, "-L", binding.origin],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise RuntimeError("otool could not inspect the macOS cryptography binding")
    dependencies = _macos_cryptography_openssl_dependencies(completed.stdout)
    if dependencies:
        names = ", ".join(sorted(dependencies))
        raise RuntimeError(
            "macOS packaging requires statically linked cryptography OpenSSL; "
            f"dynamic dependencies remain: {names}"
        )


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
    """Return an already-available uv at PINNED_UV_VERSION; never network-bootstrap.

    Candidate workflows install the reviewed binary via the SHA-pinned setup-uv
    action. Local operators must provide the same pin. This helper fails closed
    when that binary is missing rather than running an unhashed pip install.
    """

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
    raise RuntimeError(
        f"pinned uv=={PINNED_UV_VERSION} is unavailable; install the reviewed "
        "binary (for example via the SHA-pinned astral-sh/setup-uv action) "
        "before running install_locked_python"
    )


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


def _locked_build_backend_requirements(root: Path, temporary: Path) -> Path:
    """Write the complete build environment from uv.lock digests.

    Packaging, setuptools, and wheel must all be present with hashes. Partial
    success would either make ``--require-hashes`` resolve an unpinned
    dependency or let ``--no-build-isolation`` borrow ambient build tooling.
    """

    lock = tomllib.loads((root / "uv.lock").read_text(encoding="utf-8"))
    lines: list[str] = []
    missing: list[str] = []
    for name in BUILD_BACKEND_PACKAGES:
        requirement = _hash_requirement_from_lock(lock, name)
        if requirement is None:
            missing.append(name)
        else:
            lines.append(requirement)
    if missing:
        raise RuntimeError(
            "uv.lock is missing hashed build-environment packages required for locked builds: "
            + ", ".join(missing)
        )
    if len(lines) != len(BUILD_BACKEND_PACKAGES):
        raise RuntimeError(
            "uv.lock did not yield exact hashed requirements for the complete build environment"
        )
    requirements = temporary / "build-backends.txt"
    requirements.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return requirements


def _install_locked_build_backends(python: str, root: Path, temporary: Path) -> None:
    requirements = _locked_build_backend_requirements(root, temporary)
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


def _require_fresh_windows_venv(python: str) -> None:
    """Limit the uv opt-in to a fresh, isolated Windows virtual environment."""

    if platform.system() != "Windows":
        raise RuntimeError("--installer uv is supported only on Windows")
    prefix = Path(sys.prefix).resolve()
    config = prefix / "pyvenv.cfg"
    if (
        prefix == Path(sys.base_prefix).resolve()
        or Path(python).resolve().parent != prefix / "Scripts"
        or not config.is_file()
    ):
        raise RuntimeError("--installer uv requires an isolated virtual environment")
    settings = {}
    for line in config.read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition("=")
        if separator:
            settings[key.strip().lower()] = value.strip().lower()
    if settings.get("include-system-site-packages") != "false":
        raise RuntimeError("--installer uv requires system site packages to be disabled")
    # Python 3.12+ venv seeds only pip. Reject an already-used environment so
    # unrelated installed packages cannot silently survive the locked install.
    if any(
        (distribution.metadata["Name"] or "").lower() != "pip"
        for distribution in importlib.metadata.distributions(
            path=[str(prefix / "Lib" / "site-packages")]
        )
    ):
        raise RuntimeError("--installer uv requires a fresh virtual environment (only pip seeded)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", type=Path, default=REPOSITORY_ROOT)
    parser.add_argument(
        "--installer",
        choices=("pip", "uv"),
        default="pip",
        help="dependency installer (uv requires a fresh isolated Windows venv)",
    )
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
        uv_environment: dict[str, str] | None = None
        if arguments.installer == "uv":
            _require_fresh_windows_venv(python)
            # --python alone does not override configured target/prefix paths.
            # Keep all opt-in uv calls bound to our cwd and validated venv,
            # without inherited uv settings or discovered configuration files.
            uv_environment = {
                key: value for key, value in os.environ.items() if not key.upper().startswith("UV_")
            }
        uv = ensure_pinned_uv(python)
        with tempfile.TemporaryDirectory(prefix="atc-locked-") as temporary_name:
            temporary = Path(temporary_name)
            constraints = temporary / "constraints.txt"
            # Export frozen versions WITH hashes so either installer enforces digests.
            export_command = [
                uv,
                "export",
                "--frozen",
                "--no-emit-project",
                "--output-file",
                str(constraints),
            ]
            if arguments.installer == "uv":
                export_command.append("--no-config")
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
                env=uv_environment,
            )
            constraints_text = constraints.read_text(encoding="utf-8")
            if "--hash=" not in constraints_text and "sha256:" not in constraints_text:
                raise RuntimeError("uv export did not produce hash-pinned requirements")
            # Third-party packages only, hash-enforced.
            install_environment = (
                uv_environment
                if arguments.installer == "uv"
                else locked_install_environment(arguments.extra)
            )
            install_command = [python, "-m", "pip", "install"]
            requirements = ["-r", str(constraints)]
            if arguments.installer == "uv":
                install_command = [
                    uv,
                    "pip",
                    "install",
                    "--python",
                    python,
                    "--link-mode",
                    "copy",
                    "--no-config",
                ]
                if not arguments.skip_project:
                    backends = _locked_build_backend_requirements(root, temporary)
                    requirements.extend(["-r", str(backends)])
            _run(
                [*install_command, "--require-hashes", *requirements],
                cwd=root,
                env=install_environment,
            )
            verify_static_macos_cryptography(arguments.extra)
            if not arguments.skip_project:
                # Build backend must come from the reviewed lock digests, not an
                # unpinned download during uncontrolled build isolation.
                if arguments.installer == "pip":
                    _install_locked_build_backends(python, root, temporary)
                extras = ""
                if arguments.extra:
                    extras = "[" + ",".join(arguments.extra) + "]"
                # Local project: exact checkout, no dependency re-resolution, no
                # uncontrolled build isolation (build tools come from the lock).
                _run(
                    [
                        *install_command,
                        "--no-deps",
                        "--no-build-isolation",
                        "-e",
                        f".{extras}",
                    ],
                    cwd=root,
                    env=uv_environment,
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
