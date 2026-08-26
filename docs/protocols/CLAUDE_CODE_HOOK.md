# Claude Code UserPromptSubmit hook

This is an isolated, pre-generation-only runtime slice. It is not a supported
configured Claude Code client connection, an L1 lifecycle implementation, a
product exit, a release claim, or evidence of live Claude Code acceptance.

## Profile

Set `ATC_MCP_PROFILE=claude_code_hook` when starting `atc-mcp`. The profile has
a distinct server identity (`All The Context Claude Code Hook`) and exposes
only `claude_code_user_prompt_submit`. With no profile set, `atc-mcp` keeps the
ordinary `All The Context` L0 MCP server and its existing tool set and
instructions.

The tool accepts exactly these required string fields:

| Field | Maximum | Runtime use |
|---|---:|---|
| `prompt` | 4,000 characters | The only value used as an in-memory Core query |
| `cwd` | 4,096 characters | Accepted for the official hook contract, then ignored |
| `session_id` | 128 characters | Accepted for the official hook contract, then ignored |

The adapter never resolves, reads, forwards, logs, audits, or persists `cwd`
or `session_id`. It does not log, persist, propose, or return `prompt`.

## Runtime boundary

The hook requires the usual authenticated `ATC_CLIENT_ID` and
`ATC_CLIENT_TOKEN` (or the configured OS credential-store token), and accepts
only a plain `http://127.0.0.1:<port>` `ATC_TARGET_URL`. It uses only Core's
`POST /v1/context/bootstrap` path with a bounded timeout; this profile has no
Relay fallback. Core remains responsible for authentication, authorization,
retrieval filtering, and authoritative context.

The output is always valid Claude Code `UserPromptSubmit` hook JSON with only
`hookSpecificOutput.hookEventName` and `hookSpecificOutput.additionalContext`.
The latter is empty on missing credentials, revoked credentials, unavailable
Core, errors, or timeouts. Successful output contains only authorized
`items[].content`, framed as untrusted reference data rather than instructions,
within a fixed 8,000-character total budget. IDs, source references, paths,
ACL/audit metadata, and all hook input fields are excluded.

The installed MCP SDK serializes this tool's result as one text content item
containing the hook JSON. `structured_content` is intentionally not required
by this hook contract.
