# Exact publication workflow

Run from the prepared repository in PowerShell 7. Install GitHub CLI and sign in with `gh auth login`. Replace the owner once; never paste a token into these commands. Keep the repository private until the license and external validation gates below are complete.

```powershell
param([Parameter(Mandatory)][string]$Owner)
$ErrorActionPreference='Stop'
$Repo="$Owner/claude-deepseek"
# Set these to your chosen public identity (GitHub Settings > Emails shows noreply).
$CommitName=Read-Host 'Public commit name'
$CommitEmail=Read-Host 'Public commit email'
git config --local user.name $CommitName
git config --local user.email $CommitEmail
. ./scripts/Install-Gitleaks.ps1
./Test-Release.ps1
git add -- .
git diff --cached --name-status
git diff --cached --check
git diff --cached
# Inspect the displayed diff before the following commit.
git commit -m 'Prepare portable Windows workflow'
gh repo create $Repo --private --source . --remote origin --push
gh run list --repo $Repo
$RunId=gh run list --repo $Repo --limit 1 --json databaseId --jq '.[0].databaseId'
gh run watch $RunId --repo $Repo --exit-status
```

The release-files.json allowlist must be updated deliberately whenever adding a reviewed file, including LICENSE.

Check every command's exit status; stop on any failure. `Test-Release.ps1` enforces this for its checks. If the remote already exists, inspect it before attaching it; do not overwrite its history.

Independently clone the private repository into a clean Windows account and a directory with spaces. Follow README setup using fresh credentials. Verify model selection, a synthetic tool handoff, workflow resume, successful reviewer metadata, and a reported unavailable review. Confirm routing with synthetic provider failures and no refusal fallback or replay. Review local data handling. Record exact versions and results in docs/VALIDATION.md. Never upload local runtime files.

Choose a license and copyright holder, add full LICENSE text, resolve dependency-notice questions, and commit the result. Then run:

```powershell
./Test-Release.ps1 -PublicRelease
git status --short
git log --all --oneline
git branch --all
git tag --list
gh release list --repo $Repo
gh api "repos/$Repo/actions/artifacts"
```

In GitHub Settings > Security, enable and verify secret scanning, push protection, dependency graph/alerts, and private vulnerability reporting where available. In Settings > Rules, protect main against deletion and force pushes and require `offline` CI checks. Do not require an unavailable second reviewer on a solo repository. If a setting is unavailable for a private repository, record it and enable immediately after the visibility change. Review branches/tags and any artifacts or release attachments; these CI steps intentionally upload none.

Only after independent validation, license selection, and all checks pass:

```powershell
gh repo edit $Repo --visibility public --accept-visibility-change-consequences
gh repo view $Repo --json visibility
# Recheck the security settings after visibility changes.
git tag -a v0.1.0 -m 'First verified Windows release'
git push origin v0.1.0
gh release create v0.1.0 --repo $Repo --title 'v0.1.0' --notes-file docs/RELEASE_NOTES.md
```

Clone the public repository into a new directory and repeat the documented setup and launch smoke test. Do not label the release verified until this succeeds. If a credential was uploaded, rotate/revoke it before history cleanup: https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository

Security settings reference: https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/enabling-features-for-your-repository/managing-security-and-analysis-settings-for-your-repository
