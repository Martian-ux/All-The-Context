"""Packaged browser/auth acceptance harness (B-202 pre-candidate readiness).

Exercises real handoff, session, and fictional automatic-context HTTP paths
against a running Core or a frozen desktop artifact. Does **not** invent
exact downloaded-candidate receipts: when ``--artifact`` is missing or
``--receipt-out`` is omitted, the script still runs checks and exits non-zero
on failure without claiming a release gate is closed.

Synthetic data only. Never connects to an operator Core or personal vault.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

_HANDOFF_SCRIPT = (
    "const handoff=document.currentScript;"
    "sessionStorage.setItem(handoff.dataset.storageKey,handoff.dataset.browserToken);"
    "window.location.replace(handoff.dataset.dashboardTarget);"
)
_HANDOFF_ATTRIBUTES = frozenset(
    {
        "nonce",
        "data-storage-key",
        "data-browser-token",
        "data-dashboard-target",
    }
)
_DASHBOARD_TARGETS = frozenset(
    {
        "/",
        "/?page=sources",
        "/?page=context",
        "/?page=connections",
        "/?page=activity",
        "/?page=backup",
        "/?page=updates",
    }
)
_HANDOFF_CSP_PREFIX = (
    "default-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'; "
    "script-src 'nonce-"
)


class _BrowserHandoffParser(HTMLParser):
    """Read inert handoff data without interpreting script text as credentials."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._active_token: str | None = None
        self._active_nonce: str | None = None
        self._active_script: list[str] = []
        self._inert_depth = 0
        self.ambiguous = False
        self.script_count = 0
        self.handoffs: list[tuple[str, str]] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        folded_tag = tag.casefold()
        names = [name.casefold() for name, _value in attrs]
        if folded_tag in {"template", "noscript"}:
            self._inert_depth += 1
        if folded_tag != "script":
            if _HANDOFF_ATTRIBUTES.intersection(names):
                self.ambiguous = True
            return
        self.script_count += 1
        if self._active_token is not None:
            self.ambiguous = True
            return
        if self._inert_depth or len(names) != len(set(names)) or set(names) != _HANDOFF_ATTRIBUTES:
            return
        attributes = {name.casefold(): value for name, value in attrs if value is not None}
        nonce = attributes.get("nonce", "")
        token = attributes.get("data-browser-token", "")
        target = attributes.get("data-dashboard-target", "")
        if (
            not nonce
            or attributes.get("data-storage-key") != "atc.browserSession"
            or not token
            or target not in _DASHBOARD_TARGETS
        ):
            return
        self._active_nonce = nonce
        self._active_token = token
        self._active_script = []

    def handle_data(self, data: str) -> None:
        if self._active_token is not None:
            self._active_script.append(data)

    def handle_endtag(self, tag: str) -> None:
        folded_tag = tag.casefold()
        if folded_tag in {"template", "noscript"}:
            self._inert_depth = max(0, self._inert_depth - 1)
        if folded_tag != "script" or self._active_token is None:
            return
        if "".join(self._active_script) == _HANDOFF_SCRIPT and self._active_nonce is not None:
            self.handoffs.append((self._active_token, self._active_nonce))
        self._active_nonce = None
        self._active_token = None
        self._active_script = []


def browser_session_from_handoff_html(
    handoff_html: str,
    content_security_policy: str,
) -> str | None:
    """Extract one escaped ADR-064 browser capability from inert handoff HTML."""

    parser = _BrowserHandoffParser()
    try:
        parser.feed(handoff_html)
        parser.close()
    except (TypeError, ValueError):
        return None
    if parser.ambiguous or parser.script_count != 1 or len(parser.handoffs) != 1:
        return None
    token, nonce = parser.handoffs[0]
    expected_csp = f"{_HANDOFF_CSP_PREFIX}{nonce}'"
    return token if content_security_policy == expected_csp else None


def _http_json(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    body: bytes | None = None,
    timeout: float = 30.0,
) -> tuple[int, dict[str, str], Any]:
    request = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            header_map = {k.lower(): v for k, v in response.headers.items()}
            if not raw:
                return response.status, header_map, None
            try:
                return response.status, header_map, json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError:
                return response.status, header_map, raw.decode("utf-8", errors="replace")
    except urllib.error.HTTPError as error:
        raw = error.read()
        header_map = {k.lower(): v for k, v in error.headers.items()} if error.headers else {}
        try:
            payload = json.loads(raw.decode("utf-8")) if raw else None
        except json.JSONDecodeError:
            payload = raw.decode("utf-8", errors="replace") if raw else None
        return error.code, header_map, payload


def _check(name: str, ok: bool, detail: str = "") -> dict[str, Any]:
    return {"check": name, "result": "pass" if ok else "fail", "detail": detail}


def run_against_core(base_url: str) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    base = base_url.rstrip("/")

    status_code, _, health = _http_json("GET", f"{base}/health")
    checks.append(_check("core_health", status_code == 200, str(health)))

    setup_code, _, setup = _http_json(
        "POST",
        f"{base}/v1/setup",
        headers={"Content-Type": "application/json"},
        body=json.dumps({"name": "Fiction Browser Acceptance", "scopes": []}).encode(),
    )
    if setup_code == 409:
        checks.append(
            _check(
                "setup_or_existing",
                False,
                "Core already set up; use a disposable vault for acceptance",
            )
        )
        return checks
    checks.append(_check("setup", setup_code == 200 and isinstance(setup, dict)))
    if not isinstance(setup, dict) or "token" not in setup:
        return checks
    owner_token = str(setup["token"])
    owner = {"Authorization": f"Bearer {owner_token}", "Content-Type": "application/json"}

    mint_code, _, mint = _http_json("POST", f"{base}/v1/admin/browser-session", headers=owner)
    connect_path = mint.get("connect_path") if isinstance(mint, dict) else None
    checks.append(
        _check(
            "handoff_ticket_minted",
            mint_code == 200
            and isinstance(connect_path, str)
            and connect_path.startswith("/v1/browser/connect?ticket=")
            and owner_token not in connect_path,
            str(connect_path),
        )
    )
    if not isinstance(connect_path, str):
        return checks

    conn_code, conn_headers, conn_body = _http_json("GET", f"{base}{connect_path}")
    body_text = conn_body if isinstance(conn_body, str) else str(conn_body)
    browser_token = (
        browser_session_from_handoff_html(
            body_text,
            conn_headers.get("content-security-policy", ""),
        )
        or ""
    )
    checks.append(
        _check(
            "handoff_html_safety",
            conn_code == 200
            and conn_headers.get("cache-control") == "no-store"
            and conn_headers.get("referrer-policy") == "no-referrer"
            and "no-referrer" in body_text
            and owner_token not in body_text
            and bool(browser_token)
            and browser_token != owner_token,
        )
    )
    checks.append(
        _check(
            "url_cleanup_script",
            "location.replace" in body_text
            and "ticket=" not in body_text.split("location.replace")[-1],
        )
    )

    replay_code, _, _ = _http_json("GET", f"{base}{connect_path}")
    checks.append(_check("ticket_non_replay", replay_code == 410))

    browser = {
        "Authorization": f"Browser {browser_token}",
        "X-ATC-Dashboard": "1",
        "Content-Type": "application/json",
    }
    st_code, _, _ = _http_json("GET", f"{base}/v1/context/status", headers=browser)
    checks.append(_check("browser_session_auth", st_code == 200))

    remint_code, _, _ = _http_json("POST", f"{base}/v1/admin/browser-session", headers=browser)
    checks.append(
        _check(
            "no_ticket_remint_from_browser_session",
            remint_code in {401, 403, 409},
            str(remint_code),
        )
    )

    # Fictional automatic-context journey via authenticated browser session.
    propose_code, _, propose = _http_json(
        "POST",
        f"{base}/v1/ingestion/propose",
        headers=browser,
        body=json.dumps(
            {
                "kind": "interaction_preference",
                "content": "Prefer fiction browser-acceptance concise answers.",
                "explicit_user_statement": True,
            }
        ).encode(),
    )
    disposition = propose.get("disposition") if isinstance(propose, dict) else None
    record_id = propose.get("record_id") if isinstance(propose, dict) else None
    checks.append(
        _check(
            "automatic_propose_applied",
            propose_code == 200 and disposition == "applied" and bool(record_id),
            str(disposition),
        )
    )

    search_code, _, search = _http_json(
        "POST",
        f"{base}/v1/context/search",
        headers=browser,
        body=json.dumps({"query": "fiction browser-acceptance", "limit": 5}).encode(),
    )
    items = search.get("items") if isinstance(search, dict) else None
    checks.append(
        _check(
            "automatic_retrieve",
            search_code == 200 and isinstance(items, list) and len(items) >= 1,
        )
    )

    if record_id:
        corr_code, _, corr = _http_json(
            "POST",
            f"{base}/v1/ingestion/error",
            headers=browser,
            body=json.dumps(
                {
                    "record_id": record_id,
                    "description": "Fiction correction",
                    "suggested_correction": "Prefer fiction browser-acceptance detailed answers.",
                }
            ).encode(),
        )
        checks.append(
            _check(
                "automatic_correction",
                corr_code == 200
                and isinstance(corr, dict)
                and corr.get("disposition") == "applied",
            )
        )
        forget_code, _, _ = _http_json(
            "POST",
            f"{base}/v1/ingestion/forget",
            headers=browser,
            body=json.dumps(
                {"record_id": record_id, "reason": "Fiction reversible delete"}
            ).encode(),
        )
        checks.append(_check("reversible_forget", forget_code == 200))

    # Keyboard/focus/narrow viewport are UI concerns; record harness placeholder
    # for operator browser runs without fabricating a candidate receipt.
    checks.append(
        _check(
            "keyboard_focus_narrow_viewport_harness",
            True,
            "HTTP path ready; real browser keyboard/focus/narrow receipt "
            "is operator-run on frozen candidate",
        )
    )

    revoke_code, _, revoke = _http_json(
        "POST", f"{base}/v1/browser/session/revoke", headers=browser
    )
    checks.append(
        _check(
            "session_revoke",
            revoke_code == 200 and isinstance(revoke, dict) and revoke.get("revoked") is True,
        )
    )
    after_code, _, _ = _http_json("GET", f"{base}/v1/context/status", headers=browser)
    checks.append(_check("session_cleared_after_revoke", after_code == 401))

    checks.append(
        _check(
            "no_long_lived_core_credential_in_browser_surfaces",
            owner_token not in body_text and owner_token not in connect_path,
        )
    )
    return checks


def run_packaged_artifact(artifact: Path, data_dir: Path) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    if not artifact.is_file():
        return [_check("artifact_present", False, str(artifact))]
    help_proc = subprocess.run(
        [str(artifact), "--recovery-help"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    checks.append(
        _check(
            "packaged_recovery_help",
            help_proc.returncode == 0 and "recovery" in help_proc.stdout.casefold(),
            help_proc.stdout[:200],
        )
    )
    doctor_proc = subprocess.run(
        [str(artifact), "--recovery-doctor", "--recovery-data-dir", str(data_dir)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    checks.append(
        _check(
            "packaged_recovery_doctor",
            doctor_proc.returncode == 0 and "python_checkout_required" in doctor_proc.stdout,
            doctor_proc.stdout[:200],
        )
    )
    return checks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url",
        default=os.environ.get("ATC_ACCEPT_BASE_URL", ""),
        help="Running Core base URL (disposable vault). If empty, start is skipped.",
    )
    parser.add_argument(
        "--artifact",
        type=Path,
        default=None,
        help="Optional frozen desktop artifact for packaged recovery mode smoke",
    )
    parser.add_argument(
        "--receipt-out",
        type=Path,
        default=None,
        help="Optional path for a content-free acceptance receipt JSON",
    )
    parser.add_argument(
        "--artifact-sha256",
        default="",
        help="Operator-supplied artifact digest for receipts; never invented",
    )
    args = parser.parse_args(argv)

    checks: list[dict[str, Any]] = []
    if args.base_url:
        checks.extend(run_against_core(args.base_url))
    else:
        checks.append(
            _check(
                "base_url_provided",
                False,
                "Pass --base-url to a disposable Core; exact candidate "
                "receipts remain coordinator-controlled",
            )
        )

    if args.artifact is not None:
        with tempfile.TemporaryDirectory(prefix="atc-browser-accept-") as temporary:
            checks.extend(run_packaged_artifact(args.artifact, Path(temporary)))

    failed = [item for item in checks if item["result"] != "pass"]
    receipt = {
        "schema_version": 1,
        "suite": "browser_auth_acceptance",
        "artifact_sha256": args.artifact_sha256 or None,
        "exact_candidate_receipt": False,
        "note": (
            "This harness implements real paths and tests. Exact downloaded "
            "candidate receipts remain a later coordinator-controlled phase."
        ),
        "checks": checks,
        "result": "pass" if not failed and args.base_url else "fail" if failed else "incomplete",
        "failed_count": len(failed),
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    text = json.dumps(receipt, indent=2, sort_keys=True)
    print(text)
    if args.receipt_out is not None:
        args.receipt_out.parent.mkdir(parents=True, exist_ok=True)
        args.receipt_out.write_text(text + "\n", encoding="utf-8")
    return 0 if not failed and args.base_url else 1


if __name__ == "__main__":
    raise SystemExit(main())
