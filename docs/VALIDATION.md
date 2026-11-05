# Release validation — 2026-11-05

Windows-first release, licensed under MIT, copyright (c) 2026 teavert. The owner confirmed publication rights for the gateway, workflow, and reviewer integration. The exported worker supervisor is part of that locally maintained integration; inspection found no third-party source headers or vendored components. Dependencies are installed separately and retain upstream licenses. This is release verification, not a guarantee that the software has no defects.

## Offline and installation checks

Python 3.12.16, Node 24.22.2, pnpm 12.10.1, gitleaks 8.30.1. The release uses locked dependencies and read-only Windows CI with actions pinned to verified commit hashes.

- 66 gateway tests passed: routing, capacity guards, refusal/technical-failure boundaries, streaming/no replay, authentication, native transport, status isolation, GPT protocol/tool mapping, and admission.
- 27 workflow tests ran: 26 passed, one skipped because Windows symlink creation was unavailable. Worker supervisor: five tests cover cross-process admission/cleanup and Windows sharing-violation recovery. Reviewer policy, shared queue/cancellation, and offline authentication-failure tests passed.
- Setup with spaces, repeated setup, preserved settings/token, generated DPAPI helper, missing credentials, and occupied port checks passed. A separate private clone installed its own dependencies and passed the complete offline release check.
- A real isolated-clone launcher check passed through Start-ReliabilityGateway.ps1, Start-ClaudeDeepSeek.ps1, generated hooks/settings, Claude Code, and a live DeepInfra response. The clone used a fresh local gateway token and the owner's existing provider accounts. No provider credentials or session data were committed. Its gateway process was identified and stopped; the working installation was untouched.
- Repeated checks isolated the earlier intermittent worker failure to Windows status-file rename sharing. The supervisor now retries only transient Windows permission/sharing errors for at most one second, retaining atomic publication and failing closed for persistent or unrelated errors. A deterministic regression test covers the reader-sharing failure.

## Live checks

Only synthetic prompts and fixture data were submitted. No private project evidence or raw transcripts were used.

- Authenticated loopback health succeeded; an invalid token was rejected.
- DeepInfra native Anthropic route returned HTTP 200 using deepseek-ai/DeepSeek-V4.1-Flash-1017, with actual serving route verified.
- Outrun, Unlid, AllModels, and OpenRouter each returned a real completion using their configured model IDs.
- The translated chain streamed successfully through Outrun with a completed Anthropic event stream.
- GPT main completed using gpt-6-astra. A second request returned the expected dynamic tool handoff without executing that tool.
- Independent reviewer bridge completed the read-only fixture calculation CEDAR-5812|83 using ChatGPT authentication, gpt-6-astra, high effort, and read-only execution. API credential sentinels were not used.
- The updated provider-library combination passed the offline suites and translated live streaming check. Future users can run the opt-in scripts/test_live.py check with their own credentials; it uses provider credits/subscription usage and is excluded from CI.

## Dependency and content review

The first Python audit found 13 advisories in filelock 3.32.7. The release now pins filelock 4.2.9 and LiteLLM 1.109.0rc1. This official release candidate is needed because stable LiteLLM 1.108.0 still requires filelock below 4. Dependency compatibility, offline behavior, and live translation were checked; monitor upstream stable releases before updating the pins.

After that correction, pip-audit 2.10.1 reported no known vulnerabilities in all 76 locked Python dependencies; pnpm audit reported zero advisories in production Node dependencies. Audits query current advisory databases and do not establish absence of undiscovered issues. Both audits are included in CI. License metadata is recorded in PYTHON_DEPENDENCIES.json; installed libraries and executables are excluded from the repository.

The explicit release manifest, redacted gitleaks candidate scan, complete reachable-history scan, staged-diff check, and runtime-file exclusions passed. No original Git histories, keys (including encrypted keys), sessions, transcripts, databases, logs, plugin caches, binaries, or installed dependencies are included. GitHub commit authorship uses the account's public noreply identity.

## GitHub publication

Repository: https://github.com/teavert/claude-deepseek . Initial private Windows CI passed at https://github.com/teavert/claude-deepseek/actions/runs/42019047812 . The MIT/security update must also pass CI before visibility changes.

Before publication, the repository had only main, no tags, releases, release attachments, or Actions artifacts. Dependency graph and Dependabot alerts were enabled. Private branch rules and secret scanning were unavailable on the account's current plan; public secret scanning, push protection, private vulnerability reporting, and main-branch rules must be enabled and verified immediately after the visibility change. Final public settings and clone results are recorded in the release notes after completion.
