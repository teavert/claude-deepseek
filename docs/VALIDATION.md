# Validation record — 2026-11-05

Prepared and tested under the current Windows account with Python 3.12.16, Node 24.22.2, pnpm 12.10.1, and gitleaks 8.30.1. Fresh Python and pnpm dependency installs succeeded using the committed version locks.

- Gateway: 66 tests passed, including routing, fallback, native transport, streaming, status, GPT tool mapping, and worker admission.
- Workflow: 27 tests ran; 26 passed and one skipped because symlink creation was unavailable on this Windows account.
- Worker supervisor: four cross-process tests passed.
- Reviewer: policy, cross-session queue/cancellation, and hermetic subscription-authentication failure checks passed. No live Codex/model calls were used. The older test-subscription-auth.mjs and test.mjs remain opt-in live integration tools, excluded from CI.
- Installation fixture in a directory containing spaces passed: first setup, repeated setup, preserved settings/token, generated DPAPI helper execution, missing credentials, and occupied port. This fixture shares the installed dependency environment through a junction and is not a clean-account installation. It is retained in temporary storage and contains synthetic credentials only.
- Reviewed file allowlist/content checks and redacted gitleaks candidate scanning passed with no findings. A fresh root commit was created; no source histories were imported. Candidate scanning and complete new-history scanning passed before the private push.

Public release remains gated on license/provenance review, a full end-user launch with fresh provider credentials, live provider/model verification, remaining GitHub security settings, artifact review, and a public clone smoke test. The private repository teavert/claude-deepseek was created through Edge. Git uses the account's GitHub noreply identity rather than the machine's personal email. Remote CI, independent clone validation, and security-setting verification remain pending until recorded below.

Pydantic emitted dependency warnings about ReadOnly metadata during gateway tests; tests passed. Historical live-verification statements retained in routing documentation describe the original private installation, not this new release.

One intermediate full run hit an intermittent worker startup-status timeout. Four targeted reruns passed. The fixture now allows 30 seconds for startup and includes final status/process errors on failure; production admission semantics were not changed. Verify stability in independent Windows CI before public release.

## Private GitHub verification

- Private repository: https://github.com/teavert/claude-deepseek ; code commit 3d91fd138b7b452f771ca1be9dab08f232acea8f.
- Fresh GitHub Windows runner passed dependency installation, offline setup, all offline suites, and candidate/full-history secret scanning: https://github.com/teavert/claude-deepseek/actions/runs/42019047812 . No artifacts were uploaded.
- A separate authenticated private clone in a directory containing spaces installed its own Python environment and reviewer dependencies and generated fresh local configuration. The complete Test-Release.ps1 suite and secret/history scanning also passed in this clone. It uses the current Windows account, not a separate human account.
- Dependency graph and Dependabot alerts were enabled and verified in Edge. The private security settings did not offer repository secret scanning/push protection; enable and verify them when available before public release.
- GitHub explicitly says rulesets will not be enforced for this private repository under the current plan. No ineffective ruleset was saved. Configure enforced main-branch protection when available before public release.
- No license, public visibility change, tag, release attachment, or published release was created.
