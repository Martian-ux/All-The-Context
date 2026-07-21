# Permission model

Every request resolves one active `client_registration` from a constant-time
hash comparison, then checks its coarse scopes. Revocation is checked on every
request. Retrieval applies record policy before ranking or context compilation:

1. Require `context:read` (or the narrower status/proposal scope).
2. Reject expired, deleted, rejected, pending, or superseded records.
3. Intersect the request and record scopes.
4. Deny when the client is listed in `denied_clients`.
5. When `allowed_clients` is non-empty, require explicit membership.
6. On Relay, require `always_available` in addition to all previous rules.

An empty allow-list means any non-denied client with the coarse scope may read
the record. Deny always wins. Search indexes operate only after this filter and
cannot confer permission.

Administrative UI operations require `admin`. Model-facing clients are not
granted it by default. `auto_approve` is a separate opt-in capability and does
not permit model inferences or sensitive replication without the configured
approval policy.
