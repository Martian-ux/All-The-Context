# Security policy

Do not open public issues containing personal context, credentials, database
files, exports, or replication secrets. Report suspected vulnerabilities
privately to the repository owner until a dedicated disclosure address exists.

Core is loopback-only by default. Remote Relay deployments must use HTTPS,
scoped per-client credentials, a distinct replication secret, restricted data
directory access, and current dependencies. The file credential fallback is
for development only; production installations should use the OS credential
backend.

The repository threat model is
[docs/security/All The Context-threat-model.md](docs/security/All%20The%20Context-threat-model.md).
