"""Durable synthetic readers for preexisting disposable retrieval fixtures.

Fixture IDs remain stable because several ACL cases refer to them. This helper
is evaluation setup, never an adapter operation or permission granted in a real
vault. Its generated token is discarded and never returned or logged.
"""

from __future__ import annotations

import json

from allthecontext.ids import utc_now
from allthecontext.security import ClientPrincipal, generate_token, hash_token
from allthecontext.storage import CoreStore


def register_synthetic_principal(store: CoreStore, principal: ClientPrincipal) -> None:
    with store.transaction() as connection:
        connection.execute(
            "INSERT OR IGNORE INTO client_registrations"
            "(id,vault_id,name,token_hash,scopes_json,auto_approve,created_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (
                principal.id,
                store.vault_id(),
                principal.name,
                hash_token(generate_token()),
                json.dumps(sorted(principal.scopes)),
                int(principal.auto_approve),
                utc_now(),
            ),
        )
