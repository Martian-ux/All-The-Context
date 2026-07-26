"""Enforce exact-SHA quality and optional hosted nine-job matrix evidence."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, cast

from allthecontext.exact_source_gate import (
    REQUIRED_CI_JOBS,
    REQUIRED_CI_MATRIX_JOBS,
    REQUIRED_SECURITY_PARITY_JOBS,
    matrix_evidence_from_github,
    run_local_quality_gates,
    write_matrix_evidence,
)
from allthecontext.release_manifest import ManifestError

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def _get_json(url: str, token: str) -> Any:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2026-03-10",
            "User-Agent": "all-the-context-exact-source-gate",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        raise ManifestError(f"GitHub matrix preflight failed with HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise ManifestError("GitHub matrix preflight could not read workflow evidence") from exc


def verify_hosted_matrix(
    *,
    repository: str,
    source_commit: str,
    token: str,
    api_url: str,
) -> dict[str, Any]:
    base = f"{api_url.rstrip('/')}/repos/{repository}"
    query = urllib.parse.urlencode(
        {
            "head_sha": source_commit,
            "status": "completed",
            "per_page": "20",
        }
    )
    runs_payload = _get_json(f"{base}/actions/runs?{query}", token)
    if not isinstance(runs_payload, dict):
        raise ManifestError("GitHub actions runs response is malformed")
    # Select run first so jobs are fetched for the exact successful CI run.
    from allthecontext.exact_source_gate import select_successful_ci_run

    runs = runs_payload.get("workflow_runs")
    if not isinstance(runs, list):
        raise ManifestError("GitHub actions runs list is missing")
    run = select_successful_ci_run(
        [item for item in runs if isinstance(item, dict)],
        source_commit=source_commit,
    )
    run_id = run.get("id")
    if not isinstance(run_id, int):
        raise ManifestError("selected CI run is missing an id")
    jobs_payload = _get_json(f"{base}/actions/runs/{run_id}/jobs?per_page=50", token)
    evidence = matrix_evidence_from_github(
        source_commit=source_commit,
        runs_payload=runs_payload,
        jobs_payload=cast(dict[str, Any], jobs_payload),
    )
    return evidence.as_dict()


def _parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)
    local = commands.add_parser("local-quality")
    local.add_argument("--repository-root", type=Path, default=REPOSITORY_ROOT)
    matrix = commands.add_parser("hosted-matrix")
    matrix.add_argument("--repository", required=True)
    matrix.add_argument("--source-commit", required=True)
    matrix.add_argument("--token-env", default="GITHUB_TOKEN")
    matrix.add_argument(
        "--api-url", default=os.environ.get("GITHUB_API_URL", "https://api.github.com")
    )
    matrix.add_argument("--output", type=Path)
    matrix.add_argument(
        "--allow-missing",
        action="store_true",
        help="record unavailable matrix evidence without failing (not for release candidates)",
    )
    commands.add_parser("list-required-jobs")
    return root


def main() -> int:
    arguments = _parser().parse_args()
    try:
        if arguments.command == "local-quality":
            results = run_local_quality_gates(arguments.repository_root)
            print(json.dumps({"ok": True, "gates": len(results)}, sort_keys=True))
        elif arguments.command == "list-required-jobs":
            print("# matrix")
            for name in REQUIRED_CI_MATRIX_JOBS:
                print(name)
            print("# security_parity")
            for name in REQUIRED_SECURITY_PARITY_JOBS:
                print(name)
            print("# all")
            for name in REQUIRED_CI_JOBS:
                print(name)
        else:
            token = os.environ.get(arguments.token_env, "")
            if not token:
                if arguments.allow_missing:
                    payload = {
                        "schema_version": 1,
                        "source_commit": arguments.source_commit,
                        "ok": False,
                        "status": "unavailable",
                        "reason": "missing_token",
                        "required_jobs": list(REQUIRED_CI_JOBS),
                    }
                    if arguments.output is not None:
                        arguments.output.parent.mkdir(parents=True, exist_ok=True)
                        arguments.output.write_text(
                            json.dumps(payload, indent=2, sort_keys=True) + "\n",
                            encoding="utf-8",
                        )
                    print(json.dumps(payload, sort_keys=True))
                    return 0
                raise ManifestError("GITHUB_TOKEN is required for hosted matrix verification")
            try:
                evidence = verify_hosted_matrix(
                    repository=arguments.repository,
                    source_commit=arguments.source_commit,
                    token=token,
                    api_url=arguments.api_url,
                )
            except ManifestError:
                if not arguments.allow_missing:
                    raise
                evidence = {
                    "schema_version": 1,
                    "source_commit": arguments.source_commit,
                    "ok": False,
                    "status": "unavailable",
                    "reason": "matrix_not_green_or_missing",
                    "required_jobs": list(REQUIRED_CI_JOBS),
                }
            if arguments.output is not None:
                if "workflow_run_id" in evidence:
                    from allthecontext.exact_source_gate import MatrixEvidence

                    write_matrix_evidence(
                        arguments.output,
                        MatrixEvidence(
                            source_commit=str(evidence["source_commit"]),
                            workflow_run_id=int(evidence["workflow_run_id"]),
                            workflow_name=str(evidence["workflow_name"]),
                            conclusion=str(evidence["conclusion"]),
                            jobs=tuple(evidence["jobs"]),
                        ),
                    )
                else:
                    arguments.output.parent.mkdir(parents=True, exist_ok=True)
                    arguments.output.write_text(
                        json.dumps(evidence, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8",
                    )
            if evidence.get("ok") is not True and not arguments.allow_missing:
                raise ManifestError("hosted nine-job matrix evidence is not green")
            print(json.dumps({"ok": evidence.get("ok", False)}, sort_keys=True))
        return 0
    except (ManifestError, OSError) as exc:
        print(f"exact source gate error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
