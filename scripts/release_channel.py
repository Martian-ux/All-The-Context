"""Prepare and verify the signed GitHub Pages beta update channel."""

from __future__ import annotations

import argparse
from pathlib import Path

from allthecontext.release_candidate import (
    CANDIDATE_FILE_NAME,
    prepare_beta_channel,
    verify_beta_channel_site,
)
from allthecontext.release_manifest import ManifestError

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_KEYRING = REPOSITORY_ROOT / "release" / "keys.json"


def _parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--release-dir", type=Path, required=True)
    prepare.add_argument("--candidate", type=Path)
    prepare.add_argument("--candidate-sha256", required=True)
    prepare.add_argument("--keyring", type=Path, default=DEFAULT_KEYRING)
    prepare.add_argument("--repository", required=True)
    prepare.add_argument("--source-commit", required=True)
    prepare.add_argument("--output-dir", type=Path, required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("--site-dir", type=Path, required=True)
    verify.add_argument("--keyring", type=Path, default=DEFAULT_KEYRING)
    return root


def main() -> int:
    arguments = _parser().parse_args()
    try:
        if arguments.command == "prepare":
            candidate = arguments.candidate or arguments.release_dir / CANDIDATE_FILE_NAME
            index = prepare_beta_channel(
                arguments.release_dir,
                candidate_path=candidate,
                candidate_sha256=arguments.candidate_sha256,
                keyring_path=arguments.keyring,
                repository=arguments.repository,
                source_commit=arguments.source_commit,
                output_dir=arguments.output_dir,
            )
        else:
            index = verify_beta_channel_site(arguments.site_dir, keyring_path=arguments.keyring)
        print(f"verified beta channel {index['version']} from {index['source_commit']}")
        return 0
    except (ManifestError, OSError) as exc:
        raise SystemExit(f"release channel error: {exc}") from exc


if __name__ == "__main__":
    raise SystemExit(main())
