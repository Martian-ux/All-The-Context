# Permission model

Every request resolves one active `client_registration` from a constant-time
hash comparison, then checks its coarse scopes. Revocation is checked on every
request. Retrieval applies current-record policy before ranking or context
compilation:

1. Require `context:read` (or the narrower status/proposal scope).
2. Require an applied, current record; reject tentative, ignored, staged,
   expired, deleted, or superseded state.
3. Intersect the request and record scopes.
4. Deny when the client is listed in `denied_clients`.
5. When `allowed_clients` is non-empty, require explicit membership.
6. Apply the V1 availability boundary: same-device `local_only` or
   `core_available` while the same-device Core is online. Legacy
   `always_available` records carry no offline guarantee and remain subject to
   the same Core policy.

An empty allow-list means any non-denied client with the coarse scope may read
the record. Deny always wins. Search indexes operate only after this filter and
cannot confer permission.

`context:propose` and `context:ingest` permit observation submission, not a
requested disposition or unrestricted record mutation. Core assigns the
effective origin from authenticated server state and applies its versioned
policy. `context:propose` also permits the narrow `forget_context` operation:
the caller must identify one current record and supply a reason, and the result
is an audited reversible tombstone, never purge. Client-provided confidence,
basis, or source text cannot bypass hard policy. Legacy `auto_approve` grants,
where present for schema compatibility, do not override the automatic policy
and are not a user-facing setup choice.

The closed capability `witness:explicit_user_statement` is not an ordinary
operation scope. Authentication and `context:propose` alone never grant it.
Only ATC-configured same-device Codex/Claude principals that setup explicitly
assigns this class (and intentional local `admin`/`*` authorities) may attest
that a specific input was directly stated by the user. Clients cannot
self-add the grant, select a disposition, force current state, or smuggle
origin/role/force fields. Omitted or default-false `explicit_user_statement`
stays tentative; authenticated archive/provider/Relay paths cannot re-label
material as witnessed user evidence without the grant. Core-controlled
importers (no client principal) remain a separate Core assignment path. This
is a local trust grant, not cryptographic authorship proof; an authorized
malicious witness principal can lie.

Administrative UI operations require `admin`. Model-facing clients are not
granted it by default and cannot change another client's origin or scopes,
availability policy, restore deleted context, or invoke irreversible purge.
Ordinary explicit corrections still flow through observation policy and retain
their previous current-record version.
