# Hosted Edge operations

The hosted Edge is optional. Core remains useful without it and never requires
Docker. Edge may run on Linux with Docker and a persistent SQLite disk. It must
sit behind HTTPS and have exactly one durable instance for this first slice.

## Normal setup from Core

An installed user does not run the commands in this document. First-run setup
offers **Continue with web & mobile setup** by default and opens the local
dashboard directly on **Connect apps → Edge for web and mobile**. That screen
performs the Core side of the workflow:

1. **Set up Edge** creates a vault-bound enrollment bundle and recovery code.
2. **Open Render** opens the user-owned hosting flow when the release has a
   configured `ATC_EDGE_DEPLOY_URL`.
3. The user puts the enrollment bundle in the host's secret
   `ATC_EDGE_BUNDLE` field. It must never be committed or pasted into an AI
   conversation.
4. After deployment, the user pastes the service's HTTPS origin into Core.
   Core requires a cryptographic challenge proof before sending any context.
5. Core pushes approved `always_available` records and keeps synchronizing in
   the background while it is running.
6. **Connect an AI app** creates a short-lived owner link. The user then adds
   the displayed `/mcp` address to a supported provider. OAuth authorization
   and revocation are handled by Edge.

The whole control flow starts on the computer running Core. The hosting
provider and AI provider still require their own account, plan, and consent;
the installer cannot safely bypass those external steps.

## Provider reachability

Edge must have a stable public HTTPS URL because provider-hosted clients connect
from their own cloud, including when the user is on a phone. As of 2026-07-21,
[Claude custom connectors](https://support.claude.com/en/articles/11176164-use-connectors-to-extend-claude-s-capabilities)
are available across Claude web, Desktop, and mobile after account setup (Free
is limited to one custom connector). [ChatGPT developer-mode apps](https://developers.openai.com/apps-sdk/deploy/connect-chatgpt)
are supported on all plans and become available in ChatGPT mobile after being
linked on the web. Workspace policy can gate developer/custom connector setup.

## Deployment requirements

- Persistent disk mounted at `/var/lib/allthecontext`.
- `ATC_EDGE_BUNDLE` stored as a host secret.
- `ATC_RELAY_DATABASE=/var/lib/allthecontext/edge.sqlite3`.
- `ATC_RELAY_HOST=0.0.0.0` inside the container only.
- A provider-supplied public URL (`RENDER_EXTERNAL_URL`) or an exact
  `ATC_EDGE_PUBLIC_URL`.
- HTTPS termination at the hosting platform or trusted reverse proxy.
- One instance. Do not horizontally scale the SQLite service.

The included [`render.yaml`](../../render.yaml) declares the service, health
check, secret prompt, and 1 GB disk. A release can set `ATC_EDGE_DEPLOY_URL`
in Core to expose its public blueprint link. Until a public repository or image
exists, the dashboard deliberately labels that link unavailable rather than
opening a fake deployment.

For contributor-only local checks, place the enrollment bundle in the current
shell's environment and run Docker Compose. Compose publishes only on
`127.0.0.1`; it is not a mobile deployment and cannot complete the production
OAuth flow without an HTTPS public origin.

```text
docker compose up --build relay
docker compose ps
```

## Security and lifecycle

The enrollment bundle contains high-value secrets. Edge derives its proposal
encryption key from that bundle, binds the durable database to the vault,
pairing secret, and public origin, and refuses to start if a different authority
is pointed at the same disk. Dynamic OAuth client registration is closed by
default and opens for ten minutes after an owner link or explicit owner action.
Access uses OAuth 2.1 authorization code + PKCE, audience-bound access tokens,
rotating refresh-token families, and per-client revocation.

The host can read approved replicated context while serving searches; Edge is
not advertised as zero-knowledge. Remote proposals are encrypted at rest as a
bounded transport queue, but Core must import them as candidates before they
can become canonical. Raw sources, pending local candidates, `core_available`,
and `local_only` records never enter this Edge slice.

Use **Remove active data and disconnect** before deleting the hosting service. Core
requires a verified terminal response reporting no remaining records before it
forgets the connection. The Windows uninstaller uses the same rule and stops
if Edge cannot be verified, so it cannot claim remote decommissioning that did
not happen. Edge enables SQLite secure deletion, checkpoints/truncates its WAL,
and compacts the live database after removing active rows. This is not a claim
about forensic erasure from provider snapshots: delete the hosted service,
persistent disk, and backups under the provider's retention policy. If Core is
unavailable, use the saved recovery code at `/owner/recover`
to revoke apps and regain owner access.

Initial pairing requires both a valid vault-bound proof and `status: ok`; an
already-decommissioned deployment cannot be paired as a fresh Edge. During a
retry of interrupted decommission, Core may accept the same proved origin's
terminal status solely to finish the idempotent purge. Terminal state is
rechecked inside every write transaction and reinforced by SQLite triggers, so
a request that passed an earlier HTTP check cannot commit after decommission.

Back up Edge only for operational continuity. Core is authoritative and can
reconstruct the approved projection from its event history. Never restore an
Edge database over Core, reuse one Edge disk for another vault, or replicate a
whole SQLite file.
