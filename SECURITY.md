# Security policy

Do not open public issues containing personal context, credentials, database
files, exports, or replication secrets. Report suspected vulnerabilities
privately to the repository owner until a dedicated disclosure address exists.

Core is loopback-only by default. V1 has no supported hosted Relay/Edge or
automatic remote-exposure path. Do not bind Core to a public interface without
an independently reviewed encrypted transport and authentication boundary.
Clients use scoped credentials. The file credential fallback is for
development only; normal installations should use the OS credential backend.

The repository threat model is
[docs/security/All The Context-threat-model.md](docs/security/All%20The%20Context-threat-model.md).
