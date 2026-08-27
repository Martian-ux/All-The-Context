# Claude Code UserPromptSubmit hook

This is a configured Claude Code UserPromptSubmit pre-generation client backed
by the isolated hook runtime. It is not an L1 lifecycle implementation, direct-
user capture, durable formation, live/private client acceptance, provider
support, a product exit, a release claim, or evidence of live Claude Code
acceptance.

## Setup integration

The first-run wizard exposes Claude Code as a separate, opt-in choice from
Claude Desktop. Hidden packaged headless setup uses `--claude-code`; the
default remains disabled unless the wizard detects an existing Claude Code
executable. If Claude Code is not detected, install it and rerun setup.

Setup writes only the Claude Code user-scope MCP registry and settings files:
`~/.claude.json` and `~/.claude/settings.json`, or the dedicated
`ATC_CLAUDE_CODE_MCP_CONFIG` and `ATC_CLAUDE_CODE_SETTINGS` overrides. It never
writes project-local `.claude/settings*.json`, `.mcp.json`, or uses
`ATC_CLAUDE_CONFIG`. Both files are read and updated in one transaction with
exact preimage rechecks, rollback, and operation-backup cleanup after a
successful rollback. Symlinked/reparse-point paths are rejected.

The managed principal is named `Claude Code` and has exactly `context:read`.
With an OS credential store, the serialized MCP environment carries the client
ID but no bearer token. A token is serialized only when the existing explicit
`ATC_ENABLE_INSECURE_DEVELOPMENT_CREDENTIAL_FILE=1` fallback is the selected
credential storage. Managed configuration also carries the Core start command,
data directory, and `ATC_AUTO_START_CORE=1`.

Setup configures all selected clients before Core launch and dashboard handoff;
optional workspace authorization remains the final setup mutation. This
setup-only slice does not add a Claude Code dashboard connection/status,
repair, or uninstall control. Claude Desktop keeps its existing distinct
principal and configuration behavior. Ordinary MCP remains L0.

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
only a plain `http://127.0.0.1:<port>` `ATC_TARGET_URL`. Before credential
lookup or client construction it strictly probes the loopback installation
with a bounded, direct request that ignores system proxies and redirects; an
optional auto-start is followed by the same strict proof. The authenticated
request also ignores system proxies, does not follow redirects, has a bounded
timeout, and streams no more than 256 KiB before JSON parsing. This profile has
no Relay fallback. Core remains responsible for authentication, authorization,
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

## Explicit memory commands (opt-in)

The write feature is disabled by default. When explicitly selected in setup,
All The Context also installs three personal user-scope commands under
`~/.claude/commands/` (or `ATC_CLAUDE_CODE_COMMANDS_DIR`):

| Command | Exact argument meaning |
|---|---|
| `/atc-remember <statement>` | The complete `command_args` string is the user statement |
| `/atc-correct <record-id> <replacement>` | The first token is the record ID; the remaining exact text is the replacement |
| `/atc-forget <record-id> [reason]` | The first token is the record ID; the remaining exact text is the reason |

The personal command files disable model invocation. Setup refuses to replace
an existing unrelated command with one of these reserved names and preserves
all unrelated command files and settings. The explicit hook is registered only
for the anchored `UserPromptExpansion` matcher
`^(atc-remember|atc-correct|atc-forget)$`. It binds Claude Code's separate
`command_name`, `command_args`, `expansion_type`, and `command_source` fields;
it does not parse or capture the ordinary `prompt`, transcript, session,
working directory, or attachments. The official contract documents
`expansion_type=slash_command` and the exact `command_args` field in the
[Claude Code hooks reference](https://code.claude.com/docs/en/hooks#userpromptexpansion-input).

The explicit path provisions a separate `Claude Code Explicit Commands`
principal with exactly `context:propose` and
`witness:explicit_user_statement`. It calls only Core's
`/v1/claude-code/memory/remember`, `/v1/claude-code/memory/correct`, and
`/v1/claude-code/memory/forget` routes. There is no Relay fallback; absent,
unreachable, unverified, or revoked Core authority blocks the command and
writes nothing.

Before the Core request, the hook keeps at most eight pending commands in
memory for 15 seconds. Each has an opaque UUID command ID and a SHA-256
commitment over the exact action and arguments; it is never persisted. A
native MCP exact-payload elicitation, when supported by the client, is only
defense in depth: declining it blocks the write, while lack of elicitation
support does not replace the typed slash-command gesture. Hook output and
receipts contain no raw arguments. The command is blocked after handling so
the exact payload is not sent on as a model prompt.
