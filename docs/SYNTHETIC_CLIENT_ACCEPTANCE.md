# Synthetic client acceptance preparation

This test-only candidate starts at combined runtime
`27a855f8ae3321c602ab8390bdfeb78677a5c15f`. It changes no production formation,
retrieval, authority, schema or application configuration. It prepares a repeatable
acceptance journey through the actual Codex and Claude source hooks, in separate
Python processes, against a real disposable IPv4 loopback Core. It does not run
installed Codex/Claude applications or a reader model.

## Executable journey

`bench/synthetic_client_acceptance.py` owns a newly created, marked scratch root,
an ephemeral-port Core child and synthetic scoped principals. Existing directories
are refused. The subprocess environment excludes inherited model credentials,
proxy settings and ATC capabilities, uses fake client homes and a scratch vault,
disables Core auto-start and selects a null keyring. Tokens are synthetic, hidden
from principal repr and kept out of command arguments and logs. No app install,
live binding, credential copy, native inference or service registration occurs.

Run from the checkout with its existing development dependencies:

```powershell
python -B -m pytest tests/integration/test_synthetic_client_acceptance.py -q -p no:cacheprovider --basetemp <new-disposable-path>
```

| Case | Evidence asserted | Limit |
|---|---|---|
| Capture in each provider, read in the other | Supported style, layout and language arrive with record ID/version; authenticated truth matches content and has evidence | Source callbacks, not installed app dispatch |
| Correction and replay | Same record advances once; history/evidence preserved; stale predecessor remains tentative; unrelated facets stay at version 1 | Claude callback has no stable native turn ID, so duplicates may add tentative observations |
| Concurrent correction | Two real hook processes race against one predecessor; one applied, one tentative; version/history count is 2 | Scheduling cannot prescribe which correction wins |
| Cold Core restart and deletion | New Core PID, same scratch vault; truth survives restart; deleted style cannot return after replay/restart/ordinary assertion | Explicit owner forget API; native forget UI unrun |
| Project and capability boundaries | Identical display labels still isolate projects; caller hint cannot override binding; foreign direct reads 404; revoked binding 403; read/capture scopes separated | Only synthetic identities and principals |
| Empty/offline/budget states | Empty successful retrieval differs from unavailable Core; unavailable hook does not auto-start; bounded output reports budget omission | Extremely small budgets may legitimately emit no notice; this case uses 256 characters |
| Literal forged reference text | Unstructured source capture stays unformed; synthetic owner explicitly seeds normal, Core-available adversarial data; fake ID remains content while envelope resolves to truth | Explicit fixture setup, not automatic formation or model interpretation credit |
| Desired ordinary project decision | Strict expected failure retains the desired cross-provider delivery assertion | Known formation gap, not acceptance credit |

Every delivered reference is checked against the authenticated public truth route.
Capture success alone is insufficient: supported claims must actually reach a read
hook and resolve to current truth. Restart creates a fresh server process rather
than rebuilding an in-process TestClient.

The full serial journey at `b56ff65c` returned **7 passed, 2 failed, 2 strict
expected failures**. One late Codex read of the unrelated foreign project's
unchanged preference returned `retrieval_unavailable`; its cause remains unproven,
including after serial execution. The literal-data fixture was then corrected
to normal/Core-available data, authenticated reader truth and actual content
query terms; that targeted case passes. The interim per-case aggregate was
**8 passed, 1 failed, 2 expected failures**. Earlier receipts remain intact.

A later coherent full serial harness, with sanitized same-call diagnostics,
reports **9 passed, 2 strict xfails, zero unexpected failures** (415.70 seconds).
All 32 positive correction/restart/deletion journey reads have no observed
exception; all 50 read traces contain only fixed stage/status, exception classes
and numeric timing/error fields. Intentional unavailable cases expose scope,
revocation and offline failures. The historical late foreign-project Codex read
was not reproduced; it is not explained or repaired by this later pass.
It remains an unclassified product-path availability observation owned by the
hook/Core verification and bootstrap investigation, not a proven harness,
preference or binding defect. Local proof uses 0.4 seconds, bootstrap HTTP uses
2 seconds; these are diagnostic facts, not proof of a timeout cause. The observer
adds slight scheduling overhead, disables line/opcode tracing, restores the prior
tracer and records no messages, locals, response bodies, tokens or proof. It
changes no production call, timeout or retry behavior.

The two strict decision xfails are one shared Core formation capability gap:
live extraction has no `I decided` rule and formed-kind validation excludes
`project_decision`. Both providers retain one tentative, unformed observation
and successful read transport omits the decision. Owner: Core live-memory
formation/policy. This desired capability is outside the current bounded grammar;
archive framing does not grant live capture authority. Future formation may add
a second observation, failing the current raw-evidence precondition before XPASS;
the formation owner should narrow that precondition to raw `captured_user_turn`
when implementing the capability, while preserving actual delivered-truth checks.
Existing focused checks remain **265 passed, 2 inherited budget failures**.
Source-client acceptance and installed application/model acceptance remain open.

## Source evidence

The harness calls `codex_hook.handle_codex_event` and
`codex_user_prompt_submit_read`, and Claude's public submit-read, submit-capture
and stop-capture helpers. Their real `_hook_client` uses verified local Core HTTP;
the harness does not replace it. `mcp_adapter._ensure_local_core` respects
`ATC_AUTO_START_CORE=0`. Core setup, client registration, identity/binding,
ingestion, bootstrap, truth, history, revocation and forget are public HTTP routes.
The source hooks wrap references using `hook_context.REFERENCE_FRAME` and bounded
record-delivery notices. This is stronger transport coverage than replacing the
hook client with an in-process test transport, but does not prove application
hook registration, native event dispatch, MCP negotiation or model use.

## Main account routing gate

Read-only `codex login status` returned **Logged in using ChatGPT**. The supported
existing-daemon version and proxy connection failed with Windows socket error
10050 before a handshake. Repeated daemon status and proxy initialize both exit 1:
`A socket operation encountered a dead network. (os error 10050)` connecting to
`C:\Users\Noah\.codex\app-server-control\app-server-control.sock`.
Therefore same-client `account/read` with
`refreshToken:false` and `account/rateLimits/read` could not establish the main
Prolite identity or quota bucket. Native inference was stopped. Authentication
status alone cannot establish plan, account identity, routing or quota consumption.
No auth file was opened, account switched, configuration changed, credentials
copied, key provisioned, authentication started or daemon service started.

The public generated protocol schema confirms the non-refresh account parameter;
OpenAI's [app-server documentation](https://learn.chatgpt.com/docs/app-server)
describes account reads and quota buckets. A generated schema is protocol evidence,
not proof of the running account. Before later native inference, use the existing
same-client session or UI `/status` to verify the main account and record the same
quota bucket before and after one bounded useful task. No quota delta is claimed
for this lane.

## Known combined-runtime findings and later gates

The combined owner reports **3,685 passed, 2 failed, 21 skipped** in broad checks.
The two failures are inherited delivery-budget fixtures that expect formation of
a long mixed turn; they pass the project-binding base and fail the preference
base. Development remains **3/14**, compared with the binding base's **4/14**, with
mixed-decision loss. This lane retains those findings and adds strict expected
failures for ordinary project-decision delivery; it does not widen formation
authority, weaken frozen assertions or repair production code. Its budget case
uses independently supported single claims, not a workaround for the mixed-turn
formation defect. Frozen v1 and four holdouts remain unchanged and unopened.

Later authorized provisioning is needed to test actual installed hook dispatch,
native app restart, MCP registration and negotiation, app permission prompts,
real principal binding setup/revocation and reader-model use of references.
Restore-to-new-binding reconciliation also remains outside this harness. These
are **UNRUN**, not implied by source-hook HTTP success. This local test candidate
does not authorize or recommend production release.
