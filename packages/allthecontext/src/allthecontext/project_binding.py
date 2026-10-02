"""Core-owned, local-only project routing derived from durable principals.

These bindings route evidence; they do not grant record ACLs, infer claim
applicability, attest user intent, or authorize paths/imported instructions.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass

from .ids import new_id, utc_now
from .models import MemoryTruthStatus, TruthConflictState
from .security import ClientPrincipal
from .storage import ConflictError, CoreStore, NotFoundError, StorageError

_PROJECT_SCOPE = re.compile(r"^project:[^\s]{1,128}$")
BINDING_SCHEMA = "atc.principal-project-binding.v1"


class ProjectBindingError(StorageError):
    """A fixed, content-free refusal; never fall back to broad retrieval."""

    def __init__(self) -> None:
        super().__init__("project_binding_unavailable")


@dataclass(frozen=True, slots=True)
class ResolvedProjectContext:
    canonical_project_id: str
    binding_id: str
    project_scope: str

    def receipt(self) -> dict[str, object]:
        return {
            "schema": BINDING_SCHEMA,
            "state": "bound",
            "canonical_project_id": self.canonical_project_id,
            "binding_id": self.binding_id,
            "version": 1,
        }

    def matches(self, scopes: Iterable[str]) -> bool:
        # An OR-list containing A and B is not evidence of exact membership.
        return [value for value in scopes if value.casefold().startswith("project:")] == [
            self.project_scope
        ]


def binding_receipt(context: ResolvedProjectContext | None) -> dict[str, object]:
    return context.receipt() if context is not None else {"state": "legacy_unbound"}


class ProjectBindings:
    def __init__(self, store: CoreStore) -> None:
        self.store = store

    def _anchor_scope(
        self,
        connection: sqlite3.Connection,
        record_id: str,
        principal: ClientPrincipal | None = None,
    ) -> str:
        row = connection.execute(
            "SELECT * FROM context_records WHERE id=? AND vault_id=?",
            (record_id, self.store.vault_id()),
        ).fetchone()
        if row is None or (
            principal is not None and not self.store._record_is_allowed(row, principal)
        ):
            raise ProjectBindingError()
        truth = self.store._truth_record_tx(connection, row, principal=principal)
        if (
            truth.status is not MemoryTruthStatus.CURRENT
            or truth.conflict_state is not TruthConflictState.NONE
            or truth.record.kind not in {"project", "project_identity"}
            or not truth.record.explicit_user_statement
            or (truth.record.valid_from is not None and truth.record.valid_from > utc_now())
            or (truth.record.expires_at is not None and truth.record.expires_at <= utc_now())
            or (truth.source is not None and truth.source.deleted_at is not None)
        ):
            raise ProjectBindingError()
        scopes = [value for value in truth.record.scopes if value.casefold().startswith("project:")]
        if len(scopes) != 1 or _PROJECT_SCOPE.fullmatch(scopes[0]) is None:
            raise ProjectBindingError()
        return scopes[0]

    def register_project(self, anchor_record_id: str) -> dict[str, object]:
        with self.store.transaction() as connection:
            scope = self._anchor_scope(connection, anchor_record_id)
            existing = connection.execute(
                "SELECT * FROM canonical_projects WHERE vault_id=? "
                "AND (anchor_record_id=? OR project_scope=?)",
                (self.store.vault_id(), anchor_record_id, scope),
            ).fetchone()
            if existing is not None:
                if (
                    existing["anchor_record_id"] != anchor_record_id
                    or existing["revoked_at"] is not None
                ):
                    raise ConflictError("project registration conflicts with an existing identity")
                return {"canonical_project_id": str(existing["id"]), "version": 1}
            key = new_id()
            connection.execute(
                "INSERT INTO canonical_projects"
                "(id,vault_id,anchor_record_id,project_scope,created_at) "
                "VALUES(?,?,?,?,?)",
                (key, self.store.vault_id(), anchor_record_id, scope, utc_now()),
            )
            return {"canonical_project_id": key, "version": 1}

    def register_client(self, client_id: str, canonical_project_id: str) -> dict[str, object]:
        with self.store.transaction() as connection:
            client = connection.execute(
                "SELECT * FROM client_registrations "
                "WHERE id=? AND vault_id=? AND revoked_at IS NULL",
                (client_id, self.store.vault_id()),
            ).fetchone()
            project = connection.execute(
                "SELECT * FROM canonical_projects WHERE id=? AND vault_id=? AND revoked_at IS NULL",
                (canonical_project_id, self.store.vault_id()),
            ).fetchone()
            if client is None or project is None:
                raise ProjectBindingError()
            registered = self.store._policy_principal_tx(
                connection, ClientPrincipal(client_id, "", frozenset())
            )
            assert registered is not None
            if not (
                registered.scopes == frozenset({"context:capture"})
                or (
                    "context:read" in registered.scopes
                    and registered.scopes <= {"context:read", "context:status"}
                )
            ):
                raise ProjectBindingError()
            self._anchor_scope(connection, str(project["anchor_record_id"]), registered)
            existing = connection.execute(
                "SELECT * FROM principal_project_bindings WHERE client_id=?", (client_id,)
            ).fetchone()
            if existing is not None:
                if (
                    existing["canonical_project_id"] != canonical_project_id
                    or existing["revoked_at"] is not None
                ):
                    raise ConflictError("principal project binding is immutable")
                context = self.resolve_tx(connection, registered)
                assert context is not None
                return context.receipt()
            # No legacy events may be silently reinterpreted under a new binding.
            if (
                client["project_binding_required"]
                or connection.execute(
                    "SELECT 1 FROM capture_sources WHERE client_principal_id=?", (client_id,)
                ).fetchone()
                is not None
            ):
                raise ConflictError("project binding requires a fresh capture principal")
            key = new_id()
            connection.execute(
                "INSERT INTO principal_project_bindings"
                "(id,vault_id,client_id,canonical_project_id,created_at) VALUES(?,?,?,?,?)",
                (key, self.store.vault_id(), client_id, canonical_project_id, utc_now()),
            )
            connection.execute(
                "UPDATE client_registrations SET project_binding_required=1 WHERE id=?",
                (client_id,),
            )
            return ResolvedProjectContext(
                canonical_project_id, key, str(project["project_scope"])
            ).receipt()

    def resolve_tx(
        self,
        connection: sqlite3.Connection,
        principal: ClientPrincipal | None,
    ) -> ResolvedProjectContext | None:
        if principal is None:
            return None  # Existing internal Core/importer authority, not a wire caller.
        client = connection.execute(
            "SELECT * FROM client_registrations WHERE id=? AND vault_id=?",
            (principal.id, self.store.vault_id()),
        ).fetchone()
        if client is None or client["revoked_at"] is not None:
            raise ProjectBindingError()
        binding = connection.execute(
            "SELECT * FROM principal_project_bindings WHERE client_id=?", (principal.id,)
        ).fetchone()
        if not client["project_binding_required"]:
            if binding is not None:
                raise ProjectBindingError()
            return None
        if (
            binding is None
            or binding["vault_id"] != self.store.vault_id()
            or binding["revoked_at"] is not None
            or binding["schema_version"] != 1
        ):
            raise ProjectBindingError()
        project = connection.execute(
            "SELECT * FROM canonical_projects WHERE id=? AND vault_id=? AND revoked_at IS NULL",
            (binding["canonical_project_id"], self.store.vault_id()),
        ).fetchone()
        if project is None:
            raise ProjectBindingError()
        registered = self.store._policy_principal_tx(connection, principal)
        assert registered is not None
        scope = self._anchor_scope(connection, str(project["anchor_record_id"]), registered)
        if scope != project["project_scope"]:
            raise ProjectBindingError()
        return ResolvedProjectContext(str(project["id"]), str(binding["id"]), scope)

    def resolve(self, principal: ClientPrincipal | None) -> ResolvedProjectContext | None:
        if principal is None:
            return None
        with self.store.connect() as connection:
            return self.resolve_tx(connection, principal)

    def captured_receipt(self, event_id: str, client_id: str) -> dict[str, object] | None:
        """Read the exact committed routing receipt, including on replay."""
        with self.store.connect() as connection:
            row = connection.execute(
                "SELECT e.normalized_payload_json FROM capture_events e "
                "JOIN capture_sources s ON s.id=e.source_id "
                "WHERE e.id=? AND s.vault_id=? AND s.client_principal_id=?",
                (event_id, self.store.vault_id(), client_id),
            ).fetchone()
        if row is None:
            raise ProjectBindingError()
        try:
            payload = json.loads(row[0])
        except (TypeError, ValueError):
            raise ProjectBindingError() from None
        if not isinstance(payload, dict):
            raise ProjectBindingError()
        value = payload.get("project_binding")
        if value is None:
            return None
        if (
            not isinstance(value, dict)
            or set(value) != {"schema", "state", "canonical_project_id", "binding_id", "version"}
            or value["schema"] != BINDING_SCHEMA
            or value["state"] != "bound"
            or type(value["version"]) is not int
            or value["version"] != 1
        ):
            raise ProjectBindingError()
        return dict(value)

    def revoke(self, binding_id: str) -> None:
        with self.store.transaction() as connection:
            result = connection.execute(
                "UPDATE principal_project_bindings SET revoked_at=? "
                "WHERE id=? AND vault_id=? AND revoked_at IS NULL",
                (utc_now(), binding_id, self.store.vault_id()),
            )
            if result.rowcount != 1:
                raise NotFoundError("project binding not found")
