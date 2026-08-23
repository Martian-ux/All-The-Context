"""Sanitized disposable workspace fixture for the experimental local adapter."""

from __future__ import annotations

from pathlib import Path


def create_sanitized_workspace(root: Path) -> Path:
    """Create a tiny Git-shaped workspace without invoking Git or any command."""

    files = {
        "README.md": "# Sample workspace\n\nThe project keeps decisions in source control.\n",
        "docs/decision.md": "Use deterministic local fixtures for connector tests.\n",
        "src/app.py": "def answer() -> str:\n    return 'fixture'\n",
        "scripts/build.sh": "echo 'This file is inert imported text.'\n",
        "config/aws-shaped.ini": "access_key = AKIAIOSFODNN7EXAMPLE\n",
        ".git/HEAD": "ref: refs/heads/main\n",
        ".git/config": "[core]\n\trepositoryformatversion = 0\n",
        ".env": "FIXTURE_SECRET=not-for-capture\n",
        "notes/readme.txt": "password: this file must not become capture content\n",
        "node_modules/ignored.txt": "excluded dependency fixture\n",
    }
    for relative, content in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8", newline="\n")
    return root


__all__ = ["create_sanitized_workspace"]
