# claude-deepseek

Windows-first Claude Code profile with a local authenticated model gateway, native model routing, independent GPT review, and persistent evidence-based workflows.

Licensed under the [MIT license](LICENSE). Copyright (c) 2026 teavert.

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

The Python lock pins LiteLLM 1.109.0rc1 because the current stable release restricts filelock to a vulnerable major version. This official release candidate supports the patched filelock 4.2.9 pin; both offline and live integration checks are recorded in docs/VALIDATION.md.

Optional live verification (uses your provider accounts): `gateway/venv/Scripts/python.exe scripts/test_live.py`. For independent reviewer verification, run `pnpm exec node test.mjs ABSOLUTE_FIXTURE_DIRECTORY` from reviewer with a fixture.json containing `{"nonce":"CEDAR-5812","values":[19,27,37]}`. Keep fixtures and results outside Git.
