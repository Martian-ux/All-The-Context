"""Require third-party GitHub Actions to use reviewed commit SHAs."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = REPOSITORY_ROOT / "release" / "actions-policy.json"
WORKFLOWS_DIR = REPOSITORY_ROOT / ".github" / "workflows"

USES_LINE = re.compile(
    r"^(?P<prefix>\s*-?\s*uses:\s*)(?P<action>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)"
    r"@(?P<ref>[A-Za-z0-9._/-]+)\s*(?:#(?P<comment>.*))?$"
)
SHA_REF = re.compile(r"^[0-9a-f]{40}$")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", type=Path, default=REPOSITORY_ROOT)
    parser.add_argument("--policy", type=Path)
    arguments = parser.parse_args()
    root = arguments.repository_root.resolve()
    policy_path = arguments.policy or (root / "release" / "actions-policy.json")
    try:
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"actions pin error: cannot read policy: {exc}", file=sys.stderr)
        return 1
    allowed = policy.get("actions")
    if not isinstance(allowed, dict) or not allowed:
        print("actions pin error: policy actions map is missing", file=sys.stderr)
        return 1
    workflows = sorted((root / ".github" / "workflows").glob("*.yml"))
    if not workflows:
        print("actions pin error: no workflows found", file=sys.stderr)
        return 1
    errors: list[str] = []
    seen = 0
    for workflow in workflows:
        for line_number, line in enumerate(
            workflow.read_text(encoding="utf-8").splitlines(), start=1
        ):
            match = USES_LINE.match(line)
            if match is None:
                continue
            action = match.group("action")
            ref = match.group("ref")
            if action.startswith("./"):
                continue
            seen += 1
            expected = allowed.get(action)
            if not isinstance(expected, dict):
                errors.append(f"{workflow.name}:{line_number}: ungoverned action {action}")
                continue
            expected_sha = expected.get("sha")
            if not isinstance(expected_sha, str) or SHA_REF.fullmatch(expected_sha) is None:
                errors.append(f"policy entry for {action} lacks a full commit SHA")
                continue
            if SHA_REF.fullmatch(ref) is None:
                errors.append(
                    f"{workflow.name}:{line_number}: {action} must be pinned to commit SHA "
                    f"(found {ref!r})"
                )
                continue
            if ref != expected_sha:
                errors.append(
                    f"{workflow.name}:{line_number}: {action} pin {ref} does not match "
                    f"reviewed policy SHA {expected_sha}"
                )
    if errors:
        for error in errors:
            print(f"actions pin error: {error}", file=sys.stderr)
        return 1
    print(f"verified {seen} third-party Actions pins against {policy_path.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
