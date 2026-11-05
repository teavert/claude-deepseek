# Validation record — 2026-11-05

Prepared and tested under the current Windows account with Python 3.12.16, Node 24.22.2, pnpm 12.10.1, and gitleaks 8.30.1. Fresh Python and pnpm dependency installs succeeded using the committed version locks.

- Gateway: 66 tests passed, including routing, fallback, native transport, streaming, status, GPT tool mapping, and worker admission.
- Workflow: 27 tests ran; 26 passed and one skipped because symlink creation was unavailable on this Windows account.
- Worker supervisor: four cross-process tests passed.
- Reviewer: policy, cross-session queue/cancellation, and hermetic subscription-authentication failure checks passed. No live Codex/model calls were used. The older test-subscription-auth.mjs and test.mjs remain opt-in live integration tools, excluded from CI.
- Installation fixture in a directory containing spaces passed: first setup, repeated setup, preserved settings/token, generated DPAPI helper execution, missing credentials, and occupied port. This fixture shares the installed dependency environment through a junction and is not a clean-account installation. It is retained in temporary storage and contains synthetic credentials only.
- Reviewed file allowlist/content checks and redacted gitleaks candidate scanning passed with no findings. Git starts with an empty new history; no source histories were imported. Rerun history scanning after the first commit and before pushing.

Public release remains gated on license selection, an independent clean-account installation/private clone and CI, live provider/model verification with the user's own accounts, GitHub security settings, artifact review, and a public clone smoke test. The private repository teavert/claude-deepseek was created through Edge. Git uses the account's GitHub noreply identity rather than the machine's personal email. Remote CI, independent clone validation, and security-setting verification remain pending until recorded below.

Pydantic emitted dependency warnings about ReadOnly metadata during gateway tests; tests passed. Historical live-verification statements retained in routing documentation describe the original private installation, not this new release.

One intermediate full run hit an intermittent worker startup-status timeout. Four targeted reruns passed. The fixture now allows 30 seconds for startup and includes final status/process errors on failure; production admission semantics were not changed. Verify stability in independent Windows CI before public release.
