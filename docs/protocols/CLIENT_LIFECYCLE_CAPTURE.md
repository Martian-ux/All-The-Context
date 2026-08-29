# Client lifecycle capture adapter

This document defines the provider-runtime component for automatic local
conversation capture. It is an adapter contract, not a live-provider support
claim and not a replacement for the existing `ClientLifecycleEnvelope` or
reference-host formation seam.

## One opt-in, two lifecycle paths

After the client integration is enabled once, ordinary turns are handled
automatically. A read-principal `UserPromptSubmit` hook retrieves context
before generation. The capture-principal lifecycle hook observes the direct
user prompt, and `Stop` observes the rendered assistant response when the
client supplies one. No per-turn command or confirmation is required for
automatic evidence capture.

The Claude Code MCP profile exposes `claude_code_user_prompt_submit` and
`claude_code_stop`. The Codex capture profile exposes `codex_user_prompt_submit`
and `codex_stop`; the package also provides `atc-codex-hook` for the native
command-hook stdin contract. These lifecycle surfaces contain no explicit
remember, correct, or forget operation.

## Core wire contract

The adapter keeps the existing typed `ClientLifecycleEnvelope` and reference
host state internally. It does not serialize that envelope or caller-declared
authority. The authenticated Core request is flat and contains exactly:

```json
{
  "schema_version": 1,
  "event_id": "opaque bounded event ID",
  "idempotency_key": "UUIDv4",
  "session_id": "opaque bounded session ID",
  "conversation_id": "opaque bounded conversation ID",
  "sequence": 1,
  "role": "user",
  "content": "bounded prompt or rendered response",
  "observed_at": null
}
```

`role` is `user` for a direct prompt and `assistant` for a rendered response.
Core derives provider, authenticated client, source, witness, ACL,
sensitivity, and other provenance from the authenticated principal and its
registered client. The adapter does not send `provider`, `client_id`,
`witness`, `provenance`, `formation_policy`, pairing metadata, cwd, transcript
paths, attachments, or unrelated local-file data.

The endpoint is `POST /v1/lifecycle/events`. Core must return an explicit
`{"ok": true, "status": "captured"}` or `{"ok": true, "status": "replayed"}`
receipt. Any other response is non-success. The Core route and capability are
owned by the Core lane; this component only supplies the injectable typed
bridge and client method.

The shared lifecycle content limit is 16,384 characters and 65,536 UTF-8
bytes, inclusive. Provider schemas, the runtime adapter, `CaptureEventRequest`,
the client, and the Core route enforce the same content bound. The serialized
lifecycle request body is bounded at 131,072 bytes, including its envelope
metadata.

## Boundaries and failure behavior

- Only direct `http://127.0.0.1:<port>` Core targets are accepted. Credentials,
  query strings, fragments, proxies, and redirects are rejected or bypassed.
- Retrieval uses the separate read-principal bootstrap path, with bounded
  timeout, response body, and context output limits. Retrieval failure returns
  empty context and does not block the provider turn.
- Capture failure is reported internally as unavailable or rejected; the hook
  does not claim success and does not block the provider turn.
- Content is bounded and high-confidence secret-like content is rejected before
  the capture request. Raw prompt/response text is not logged or placed in the
  existing `ClientLifecycleEnvelope` payloads; it is sent only as the bounded
  content required by the authenticated local Core capture contract.
- Correlation keys are one-way digests and the exposed IDs are opaque and
  bounded. The in-memory ledger is capped at 256 turns and stores no raw client
  session ID, cwd, transcript path, attachment, or prompt/response text.
- When both lifecycle events share a reliable client turn identity and the
  adapter observes the prompt, the completion is paired to that prompt. Claude
  Code supplies no stable per-turn identifier: repeated prompts in one session
  are therefore distinct observations, Stop observations are marked unpaired,
  and no exactly-once retry claim is made for them. A completion arriving
  without a reliable turn identity is never promoted to a direct-user witness.

Automatic lifecycle events are evidence for Core policy. They are not direct
canonical-memory mutations, and the existing explicit mutation path remains
separate and approval-gated.

## Integration assumptions

The integrated Core exposes the authenticated lifecycle route using the flat
request and explicit receipt above. Transactional setup registers the Claude
and Codex capture profiles and exact event-specific tools only after the
single false-by-default opt-in. The separate Core contract documents formation,
sensitivity, ACL, and operational-secret behavior.
