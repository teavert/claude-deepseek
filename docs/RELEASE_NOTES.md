# v0.1.0

MIT-licensed Windows Claude Code profile with an authenticated loopback gateway, native model routing, independent GPT planning/review, bounded worker admission, and persistent workflow checkpoints.

Python 3.12, Node 24, pnpm 12.10.1, Claude Code, Codex ChatGPT login, and separately configured provider accounts are required. Android/REA is optional. Conversations go to the selected provider; reviewer jobs send their scoped evidence to OpenAI. Credentials and local runtime state are excluded from Git.

Offline suites, isolated installation/launcher checks, native and translated live provider requests, GPT tool handoff, independent reviewer authentication, dependency audits, and redacted source/history scans passed. One local symlink test was skipped because the Windows account lacked symlink creation capability. See docs/VALIDATION.md for details.

The lock uses tested LiteLLM 1.109.0rc1 with filelock 4.2.9 to resolve known filelock advisories while retaining a compatible dependency set. Provider availability and capacity are snapshots, not guarantees. Dependencies remain under their upstream licenses.
