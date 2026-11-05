[CmdletBinding()]
param([switch]$Offline, [switch]$SkipCredentials)
$ErrorActionPreference = 'Stop'
if ($env:OS -ne 'Windows_NT') { throw 'Windows is required.' }
function Check-Exit($step) { if ($LASTEXITCODE -ne 0) { throw "$step failed." } }
$python = (Get-Command python.exe -ErrorAction Stop).Source
$node = (Get-Command node.exe -ErrorAction Stop).Source
& $python -c 'import sys; assert sys.version_info[:2] == (3,12), "Python 3.12 required"'
Check-Exit 'Python version check'
if ((& $node --version) -notmatch '^v24\.') { throw 'Node 24 required.' }
Get-Command pnpm -ErrorAction Stop | Out-Null
Push-Location "$PSScriptRoot/reviewer"
try { if ((& pnpm --version) -ne '12.10.1') { throw 'Install pnpm 12.10.1 (the pinned package manager).' } } finally { Pop-Location }
if (-not $Offline) {
    foreach ($command in @('claude.exe','codex.exe')) { Get-Command $command -ErrorAction Stop | Out-Null }
    if (-not (Test-Path "$PSScriptRoot/gateway/venv/Scripts/python.exe")) {
        & $python -m venv "$PSScriptRoot/gateway/venv"; Check-Exit 'Virtual environment creation'
    }
    & "$PSScriptRoot/gateway/venv/Scripts/python.exe" -m pip install -r "$PSScriptRoot/gateway/requirements.lock.txt"
    Check-Exit 'Python dependency installation'
    & "$PSScriptRoot/gateway/venv/Scripts/python.exe" -m pip check
    Check-Exit 'Python dependency compatibility'
    Push-Location "$PSScriptRoot/reviewer"
    try { & pnpm install --frozen-lockfile; Check-Exit 'Reviewer dependency installation' } finally { Pop-Location }
}
$venvPython = Join-Path $PSScriptRoot 'gateway/venv/Scripts/python.exe'
if (-not (Test-Path $venvPython)) { throw 'Install dependencies before using -Offline.' }
$replacements = @{ '{{ROOT}}'=$PSScriptRoot.Replace('\','/'); '{{PYTHON}}'=$venvPython.Replace('\','/'); '{{NODE}}'=$node.Replace('\','/') }
function Generate($source, $destination) {
    if (Test-Path -LiteralPath $destination) { return }
    $text = Get-Content -LiteralPath $source -Raw
    foreach ($token in $replacements.Keys) { $text=$text.Replace($token,$replacements[$token]) }
    [IO.File]::WriteAllText($destination,$text,[Text.UTF8Encoding]::new($false))
}
Generate "$PSScriptRoot/templates/gateway.example.json" "$PSScriptRoot/gateway/config.json"
Generate "$PSScriptRoot/templates/settings.example.json" "$PSScriptRoot/config/settings.json"
Generate "$PSScriptRoot/templates/mcp.example.json" "$PSScriptRoot/config/mcp.json"
Generate "$PSScriptRoot/templates/CLAUDE.example.md" "$PSScriptRoot/config/CLAUDE.md"
Generate "$PSScriptRoot/reviewer/config.example.json" "$PSScriptRoot/reviewer/config.json"
# The slash command is a template read by Claude; resolve its path at runtime via CLAUDE.md.
$localKey = Join-Path $PSScriptRoot 'gateway-key.dpapi'
if (-not (Test-Path $localKey)) {
    $bytes = New-Object byte[] 32
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    $secret = [Convert]::ToBase64String($bytes) | ConvertTo-SecureString -AsPlainText -Force
    $secret | ConvertFrom-SecureString | Set-Content -LiteralPath $localKey
}
if (-not $SkipCredentials) {
    foreach ($provider in @('deepinfra','openrouter','outrun','unlid','allmodels')) {
        $path = Join-Path $PSScriptRoot "$provider-key.dpapi"
        if (-not (Test-Path $path)) {
            $secret = Read-Host "$provider API key (required for the matching configured route)" -AsSecureString
            if ($secret.Length -eq 0) { throw "No credential supplied for $provider." }
            $secret | ConvertFrom-SecureString | Set-Content -LiteralPath $path
            $secret = $null
        }
    }
}
Write-Host 'Setup complete. Existing settings and credentials were preserved. Model/provider availability must be verified separately.'
