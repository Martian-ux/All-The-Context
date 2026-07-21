# Retrieval and context compilation

`RetrievalBackend` is a replaceable interface. The initial implementation uses
SQLite FTS5 plus structured SQL filters and recency ordering; no embedding is
canonical or required.

The context compiler receives task/query text, client identity, requested
scopes, an optional project, and a character budget. It returns mandatory
interaction preferences first, then relevant facts/projects/decisions while
deduplicating and stopping at the budget. The response includes provenance,
omitted/unavailable scopes, a mode (`local_core`, `relay_only`,
`relay_plus_core`, or `reduced_context`), and an audit trace ID.

Permission, validity, deletion, and supersession are hard predicates applied
before relevance. A future semantic backend may rank only the already-permitted
candidate set and its indexes must be rebuildable from canonical records.
