# Hosted Relay operations

Relay may run on Linux with Docker. Core itself never requires Docker. Relay
must be deployed behind HTTPS termination and authenticated with distinct
client and replication credentials.

For a local operational check, set `ATC_RELAY_REPLICATION_SECRET` (at least 32
UTF-8 bytes) and `ATC_RELAY_BEARER_TOKEN` in the process environment. Optionally
set `ATC_RELAY_CLIENTS_JSON` to a JSON object containing scoped client tokens.
Then run:

```text
docker compose up --build relay
docker compose ps
```

The Compose mapping binds the host side to `127.0.0.1` by default. A production
reverse proxy or load balancer should be the only internet-facing component.
Its private upstream connects to the container port.

Use a high-entropy replication secret distinct from user-facing bearer tokens.
Rotate by updating Core and Relay together during a paused replication window.
Never place real secrets in Compose files, images, logs, or source control.

Back up the Relay database only for operational continuity. Core remains the
authority and can reconstruct the approved replica by replaying or rebuilding
events; never restore Relay state over Core.
