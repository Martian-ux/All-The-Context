# Client setup and seamless operation

The intended user experience is one-time registration. After installation, a
user creates a scoped client once, adds the generated STDIO configuration to
their AI client, and restarts that client. Normal sessions can then retrieve
context and propose durable changes without repeated setup or manual copy and
paste.

The user's approval policy remains the authority. Automatic proposals do not
bypass sensitivity, scope, provenance, or client authorization checks. The
dashboard is needed only for exceptions, review, imports, and administration.

Example configurations and their capability-verification status live in
[`integrations/`](../../integrations/README.md). Test a setup with the
read-only `context_status` tool before attempting ingestion.

For cloud or mobile clients, point an HTTP-capable MCP client at the stable
Relay endpoint over HTTPS. Relay serves only approved `always_available`
records and may queue proposals; it cannot create canonical memory.
