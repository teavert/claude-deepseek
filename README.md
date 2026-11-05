# claude-deepseek

Windows-first Claude Code profile with a local authenticated model gateway, native model routing, independent GPT review, and persistent evidence-based workflows.

**Release preparation: license pending. Do not publish publicly until LICENSE is selected and release checks pass.**

Use PowerShell 7. Install Windows, Python 3.12, Node 24, pnpm 12.10.1, Claude Code, and Codex CLI. Put their executables on PATH. Sign into Codex with your own ChatGPT account. Provider accounts and model access are separate requirements. This project does not include accounts or credentials.

```powershell
.\Setup.ps1
.\Test-Release.ps1
.\Start-ClaudeDeepSeek.ps1
```

Setup prompts securely for DeepInfra, OpenRouter, Outrun, Unlid, and AllModels credentials, encrypts them for the current Windows account, and generates a fresh local token. The complete preset requires all configured provider credentials. Credentials are never supplied as command arguments. Existing files are preserved: to change settings edit the ignored generated files; to change a credential remove only its local encrypted file and rerun setup.

Use `/model` for model selection. For substantive work, the lead obtains frontier-reviewer planning, starts/resumes `/workflow`, checkpoints evidence and changes, and obtains finding-validator validation before final claims. Missing GPT access is reported as unavailable. Workflow/default agents inherit the main selection. See workflow/PROTOCOL.md and gateway/MODEL-ROUTING.md.

The example defaults to DeepSeek. Provider IDs and advertised capacities are a snapshot, not an availability guarantee. Outrun limits are conservative local guards. Verify access before relying on any preset; model unavailability is not proof of a working fallback.

Setup generates config/settings.json, config/mcp.json, config/CLAUDE.md, gateway/config.json, and reviewer/config.json. Templates remain tracked. Normal Claude permissions apply. No dangerous-permission bypass is enabled.

Use `Setup.ps1 -Offline -SkipCredentials` only after dependencies exist for local fixture testing. `Test-Release.ps1` uses mocked providers; it makes no paid model requests. Run `. ./scripts/Install-Gitleaks.ps1` to load the checksum-verified pinned scanner for the current shell. Public validation also needs `-PublicRelease`.

See docs/ARCHITECTURE.md, docs/PUBLISH.md, docs/ANDROID.md, SECURITY.md, and THIRD_PARTY_NOTICES.md. Validation results and untested steps are recorded in docs/VALIDATION.md.
