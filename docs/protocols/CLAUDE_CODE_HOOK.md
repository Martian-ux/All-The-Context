# Claude Code lifecycle hook

This is a configured Claude Code UserPromptSubmit/Stop lifecycle client backed
by the isolated hook runtime. It is not a live/private client acceptance,
provider support claim, product exit, release claim, or evidence of live Claude
Code acceptance.

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

The lifecycle capture bridge additionally requires the authenticated Core
lifecycle-capture capability. This adapter lane does not change principal or
setup registration; setup integration must provide that capability for the
configured capture client while keeping any read-principal separation required
by the client contract.

Setup configures all selected clients before Core launch and dashboard handoff;
optional workspace authorization remains the final setup mutation. This
setup-only slice does not add a Claude Code dashboard connection/status,
repair, or uninstall control. Claude Desktop keeps its existing distinct
principal and configuration behavior. Ordinary MCP remains L0.

## Profile

Set `ATC_MCP_PROFILE=claude_code_hook` when starting `atc-mcp`. The profile has
a distinct server identity (`All The Context Claude Code Hook`) and exposes
only the lifecycle tools `claude_code_user_prompt_submit` and
`claude_code_stop`. With no profile set, `atc-mcp` keeps the ordinary `All The
Context` L0 MCP server and its existing tool set and instructions.

The tool accepts exactly these required string fields:

| Field | Maximum | Runtime use |
|---|---:|---|
| `prompt` | 4,000 characters | The only value used as an in-memory Core query |
| `cwd` | 4,096 characters | Accepted for the official hook contract, then ignored |
| `session_id` | 128 characters | Used only to derive an opaque bounded correlation key |

The adapter never resolves, reads, forwards, logs, audits, or persists `cwd`.
The raw `session_id` is used only in memory to derive an opaque correlation
key; it is never sent to Core. The prompt is captured as bounded content by the
authenticated local Core lifecycle contract and is never logged or returned
as hook output. No per-turn command or confirmation is needed after setup
opt-in.

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

The Stop tool accepts Claude Code's bounded `last_assistant_message`, ignores
`cwd` and `stop_hook_active`, and returns an empty Stop hook result. Its only
side effect is an authenticated, bounded `assistant` lifecycle capture. The
assistant event carries host-observation provenance internally; it is not
treated as user-authored evidence.

## Explicit memory commands (opt-in)

The write feature is disabled by default. When explicitly selected in setup,
All The Context also installs three personal user-scope skills at
`~/.claude/skills/atc-{remember,correct,forget}/SKILL.md` (or beneath the
`ATC_CLAUDE_CODE_SKILLS_DIR` override):

| Command | Exact argument meaning |
|---|---|
| `/atc-remember <statement>` | The complete `command_args` string is the user statement |
| `/atc-correct <record-id> <replacement>` | The first token is the record ID; the remaining exact text is the replacement |
| `/atc-forget <record-id>` | The record ID is the only argument; trailing text is rejected |

The personal skill files disable model invocation. Setup refuses to replace
an existing unrelated skill with one of these reserved names and preserves
all unrelated skill files and settings. The explicit hook is registered only
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
native MCP exact-payload elicitation is the authoritative user approval and
adoption of the proposed durable-memory payload: only an explicit
`confirm=true` response permits the Core request; missing, failed, timed-out,
or declined elicitation fails closed. The normal UX begins with one of the
reserved slash commands, but the registered MCP tool is also model-visible;
`command_source` and other hook metadata do not prove that the originating
prompt was personally typed by the user. A direct tool call therefore still
cannot write silently and must obtain the same native confirmation over the
exact payload.
Hook output and receipts contain no raw arguments. An ambiguous transport
failure is retried once with the identical idempotency key; if the outcome
remains ambiguous, the hook reports an unknown outcome and tells the user to
verify before repeating. The command is blocked after handling so the exact
payload is not sent on as a model prompt.

Claude Code documents MCP elicitation generally, but this branch has not yet
proved nested `Context.elicit` from an `mcp_tool` hook in a real Claude Code
session. That capability remains a live-client acceptance item. If the nested
elicitation is unavailable, the implementation fails closed and writes
nothing; this local slice does not claim live Claude Code write acceptance.
