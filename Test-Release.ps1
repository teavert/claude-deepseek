[CmdletBinding()]
param([switch]$PublicRelease)
$ErrorActionPreference='Stop'
Set-Location $PSScriptRoot
if ($PublicRelease -and -not (Test-Path LICENSE)) { throw 'Choose and add the release LICENSE before public publication.' }
function Run($program, $arguments) { & $program @arguments; if ($LASTEXITCODE -ne 0) { throw "Check failed: $program" } }
$node=(Get-Command node.exe -ErrorAction Stop).Source
$python=Join-Path $PSScriptRoot 'gateway/venv/Scripts/python.exe'
Run $python @('scripts/check_content.py')
& ./scripts/Test-Install.ps1
Run $python @('-m','unittest','discover','-s','gateway','-p','test_*.py')
Run $python @('-m','unittest','discover','-s','workflow','-p','test_*.py')
Run $python @('-m','unittest','discover','-s','codex-workers','-p','test_*.py')
Push-Location reviewer
try {
    Run 'pnpm' @('exec',$node,'test-policy.mjs')
    Run 'pnpm' @('exec',$node,'test-worker-queue.mjs')
    Run 'pnpm' @('exec',$node,'test-auth-offline.mjs')
} finally { Pop-Location }
Get-Command gitleaks -ErrorAction Stop | Out-Null
Run $python @('scripts/check_content.py','--gitleaks')
if ((& git rev-list --all --count) -ne '0') { Run 'gitleaks' @('git','--redact','--no-banner','--config','.gitleaks.toml','--log-opts=--all','.') }
if ((& git rev-list --all --count) -eq '0') { Write-Host 'History is empty; rerun this check after committing and before pushing.' }
Write-Host 'Offline release checks passed. Clean-account installation and live model checks require separate verification.'
