"""Small synchronous client used by CLI and MCP forwarding processes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx


class ContextApiError(RuntimeError):
    """A stable API error that is safe to return through MCP."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message

    def as_dict(self) -> dict[str, Any]:
        return {"ok": False, "error": {"code": self.code, "message": self.message}}


@dataclass(slots=True)
class ContextHttpClient:
    """Forward calls to a Core or Relay without embedding authority logic."""

    base_url: str
    client_id: str
    token: str
    timeout_seconds: float = 30.0

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        headers = {
            "Authorization": f"Bearer {self.token}",
            "X-ATC-Client-ID": self.client_id,
            "Accept": "application/json",
        }
        try:
            response = httpx.request(
                method,
                f"{self.base_url.rstrip('/')}{path}",
                headers=headers,
                json=json,
                params=params,
                timeout=self.timeout_seconds,
            )
        except httpx.HTTPError as exc:
            raise ContextApiError(503, "target_unavailable", str(exc)) from exc
        if response.is_error:
            code = "http_error"
            message = response.text[:500]
            try:
                body = response.json()
                detail = body.get("detail", body)
                if isinstance(detail, dict) and isinstance(detail.get("error"), dict):
                    detail = detail["error"]
                if isinstance(detail, dict):
                    code = str(detail.get("code", code))
                    message = str(detail.get("message", detail))
                else:
                    message = str(detail)
            except ValueError:
                pass
            raise ContextApiError(response.status_code, code, message)
        if response.status_code == 204:
            return {"ok": True}
        return response.json()

    def bootstrap_context(self, payload: dict[str, Any]) -> Any:
        try:
            return self._request("POST", "/v1/context/bootstrap", json=payload)
        except ContextApiError as exc:
            if exc.status_code not in {404, 405}:
                raise
            relay = self._request(
                "GET",
                "/v1/context/search",
                params={
                    "query": payload.get("task_description", payload.get("query", "")),
                    "scope": payload.get("requested_scopes", []),
                    "limit": 50,
                },
            )
            return {
                "items": relay.get("items", []),
                "context_mode": "relay_only",
                "omitted_scopes": [],
                "audit_trace_id": None,
            }

    def search_context(self, payload: dict[str, Any]) -> Any:
        try:
            return self._request("POST", "/v1/context/search", json=payload)
        except ContextApiError as exc:
            if exc.status_code not in {404, 405}:
                raise
            return self._request(
                "GET",
                "/v1/context/search",
                params={
                    "query": payload.get("query", ""),
                    "scope": payload.get("scopes", []),
                    "limit": payload.get("limit", 20),
                },
            )

    def get_context_item(self, record_id: str) -> Any:
        return self._request("GET", f"/v1/context/{record_id}")

    def context_status(self) -> Any:
        return self._request("GET", "/v1/context/status")

    def begin_ingestion(self, payload: dict[str, Any]) -> Any:
        return self._request("POST", "/v1/ingestion/begin", json=payload)

    def submit_context_batch(self, payload: dict[str, Any]) -> Any:
        return self._request("POST", "/v1/ingestion/batch", json=payload)

    def finish_ingestion(self, payload: dict[str, Any]) -> Any:
        return self._request("POST", "/v1/ingestion/finish", json=payload)

    def propose_memory(self, payload: dict[str, Any]) -> Any:
        try:
            return self._request("POST", "/v1/ingestion/propose", json=payload)
        except ContextApiError as exc:
            if exc.status_code not in {404, 405}:
                raise
            scopes = payload.get("scopes", [])
            relay_payload = {
                "idempotency_key": payload["idempotency_key"],
                "kind": payload["kind"],
                "content": payload["content"],
                "scope": scopes,
                "confidence": payload.get("confidence"),
                "sensitivity": payload.get("sensitivity", "normal"),
                "availability": "core_available",
                "provenance": {
                    "source_reference": payload.get("source_reference"),
                    "evidence": payload.get("evidence"),
                },
            }
            return self._request("POST", "/v1/proposals", json=relay_payload)

    def report_context_error(self, payload: dict[str, Any]) -> Any:
        status = self.context_status()
        if isinstance(status, dict) and "relay_writable" in status:
            relay_payload = {
                "record_id": payload["record_id"],
                "content": payload["description"],
                "evidence": payload.get("suggested_correction"),
            }
            return self._request("POST", "/v1/ingestion/error", json=relay_payload)
        return self._request("POST", "/v1/ingestion/error", json=payload)
