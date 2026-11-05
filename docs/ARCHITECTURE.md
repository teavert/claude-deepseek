# Architecture and data flow

Claude Code executes tools under its own permission checks. The launcher uses an isolated profile and starts a token-authenticated gateway bound to 127.0.0.1:4012. Provider credentials remain local and are decrypted by Windows DPAPI. Do not expose the gateway through a public interface.

Selected DeepSeek routes send conversation messages, instructions, tool schemas, and supported attachments to the selected provider. Eligible pre-output technical failures may send the same request to configured fallback providers. Refusals do not trigger provider switching; output is never automatically replayed after delivery begins.

Selecting GPT main sends role-labeled conversation history, instructions, schemas, and supported images through Codex app-server with the user's ChatGPT login. Codex is not given the host execution environment; Claude executes returned tool calls. Authentication and model access are checked by the integration, not supplied by this project.

The independent reviewer MCP bridge sends only the scoped task and supplied evidence through a read-only Codex job. Its agents must avoid credentials, raw transcripts, and unrelated private data. Review metadata must identify actual results or unavailability. API credential variables are stripped; there is no silent API billing fallback. A shared Windows supervisor bounds concurrent workers and cleans up child processes.

Workflow state, reviewer jobs, profile histories, and route metrics are local ignored data. They may contain sensitive project information and must not be attached to issues or release archives. Metadata-only gateway logging does not make every other local data source safe to share.
