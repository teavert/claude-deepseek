# Native model selection

Start `claude-deepseek` and use the existing `/model` picker:

| Picker choice | Main model | Technical-failure fallback |
| --- | --- | --- |
| DeepSeek V4.1 Flash 1017 uncensored chains + GPT Astra high as reviewer/second brain | First capacity-compatible provider in Outrun → Unlid → AllModels | Remaining providers in that order, on eligible technical failures |
| GPT Astra high → DeepSeek → existing fallback chain | `gpt-6-astra`, high effort, ChatGPT subscription | DeepInfra DeepSeek, then its existing fallback chain if needed |
| DeepSeek + GPT Astra high as reviewer/second brain → existing fallback chain | `deepseek-ai/DeepSeek-V4.1-Flash-1017`, DeepInfra Priority, max reasoning | Outrun -> Unlid -> AllModels, subject to capacity checks |

Direct commands also work:

```
/model gpt-6-astra
/model deepseek-v4.1-flash-1017-uncensored-chains
/model deepseek-ai/DeepSeek-V4.1-Flash-1017
```

The uncensored-chains name is a local routing alias, not a claim that all providers serve the exact DeepInfra checkpoint. It uses the existing provider model IDs and capacity checks, with DeepInfra token counting but no DeepInfra generation. GPT Astra high planning/review remains independent. Selecting the chain is not counted as a failure or switch. The saved default remains DeepInfra DeepSeek.

These are routing presets. The fallback order belongs to the chosen preset;
the native picker does not add a separate fallback selector. The gateway handles
failover so existing retry, timeout, capacity and refusal rules remain in force.
Claude's native `fallbackModel` setting is not needed for these presets.
Each subsequent request tries the selected main model again. Refusals and policy
errors do not trigger fallback, and delivered output is never replayed.

The selected request model is authoritative. The old `/gptswitch` command has been
retired, and its saved state is ignored. There is no extra session toggle to keep
in sync. The launcher clears an inherited `ANTHROPIC_MODEL` override so the native
picker's saved choice can take effect. DeepSeek remains the initial default;
in the picker, Enter saves the choice as the default and `s` selects it only for
the current session. Workflow and default subagents inherit the current `/model` selection via
`CLAUDE_CODE_SUBAGENT_MODEL=inherit`, rather than pinning them to DeepSeek.
Explicit per-agent model choices, including Kimi support, remain available. Restart Claude once to load the new picker entries.

## Authentication and tools

Claude uses the existing protected local gateway credential through
`Get-GatewayKey.ps1` (`apiKeyHelper`). The gateway separately uses Codex app-server
with the existing ChatGPT login for GPT; no new API key is required.
GPT requests explicitly select Astra with high effort and prohibit provider model
substitution. The adapter attaches no Codex execution environment. Claude's tools
are exposed under stable host aliases, then calls are mapped back to their exact
original names before Claude performs its permission checks and execution.
The independent GPT reviewer bridge is unchanged.

## Status and adapter boundaries

The status line distinguishes the selected preset from the actual serving model.
It retains the latest tool-capable request; text-only/background requests have a
separate line and cannot overwrite that request's metrics. The separate reviewer
line is labeled `GPT review`. Historical switch counts are not current failures.

GPT responses are buffered until a text response or tool-call handoff is ready.
Each completion starts an ephemeral Codex thread containing role-labeled host
history, with private thinking omitted. Images are attached; unsupported document
inputs fall back to DeepSeek. Only one tool call is handed off per completion.
Conversation, system instructions and tool schemas are sent to GPT when selected.

The app-server interface does not expose strict Anthropic sampling, stop-sequence
or output-token controls. The output budget is an instruction for GPT, while
DeepSeek fallback retains the exact original budget and sampling fields. Token
counting/context display still uses the DeepSeek tokenizer and the profile's
1,048,576-token window; it is not a measurement of GPT capacity. No deliberate
history truncation occurs. GPT context errors can use the DeepSeek fallback.
GPT setup and response share the existing 300-second execution budget after
admission to the worker queue. Waiting for a worker does not spend this budget.

## Shared Codex worker queue

GPT main and the independent subscription reviewer use the same Windows supervisor
at `codex-workers/runner.py`. It enforces **two active
Codex workers total**, across all gateway instances and reviewer sessions for this
user. Additional requests wait in FIFO admission order in its SQLite queue.
Authentication checks also acquire a slot; reviewer authentication happens after
admission, before any task is forwarded. Reviewer `delegate` returns a `queued`
job ID immediately. Poll `result` while either `queued` or `running`.

Cancellation removes queued work without launching Codex. A Windows Job Object
kills owned workers if their supervisor exits, including crashes and timeouts.
Stale queue records are reclaimed using process IDs and creation times. Queue
records contain process metadata only, not task text or credentials. Each waiting
request has a lightweight Python supervisor; it does not start a Codex instance.
This limit covers these two adapters, not unrelated Codex desktop/CLI processes.
Changes take effect on the next gateway/reviewer start; restart older Claude
sessions to load the updated reviewer bridge.

## Maintenance

Gateway revision 8 adds the shared two-worker queue. The launcher rejects older
instances. Restart only the verified profile gateway when authenticated `/health`
reports no active requests. Port 4011 is an independent older gateway.
Logs contain routing/usage metadata, not conversations or credentials.

Tests: `gateway/venv/Scripts/python.exe -m unittest discover -s gateway -p "test_*.py"`.
Native selection and GPT adapter cases are in `test_model_routing.py`; fallback,
status, native transport, and workflow suites cover the existing behavior.
The original private installation previously checked the native picker interactively in both directions with real GPT and
DeepInfra replies. A simulated GPT timeout with the native GPT model ID also
produced a real DeepInfra fallback response. Tests cover exact provider model IDs,
including normalization of the native Default option's `[1m]` context marker.

References: [Claude model picker](https://code.claude.com/docs/en/settings-reference#modelpicker),
[Codex app-server](https://learn.chatgpt.com/docs/app-server).
