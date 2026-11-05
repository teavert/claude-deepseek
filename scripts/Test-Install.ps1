$ErrorActionPreference='Stop'
$source=Split-Path $PSScriptRoot
$fixture=Join-Path ([IO.Path]::GetTempPath()) ('claude deepseek install '+[Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory $fixture | Out-Null
$files=& git -C $source ls-files --cached --others --exclude-standard
if ($LASTEXITCODE -ne 0) { throw 'Cannot enumerate release files.' }
foreach ($file in $files) {
    $target=Join-Path $fixture $file
    New-Item -ItemType Directory -Force (Split-Path $target) | Out-Null
    Copy-Item -LiteralPath (Join-Path $source $file) -Destination $target
}
New-Item -ItemType Junction -Path (Join-Path $fixture 'gateway/venv') -Target (Join-Path $source 'gateway/venv') | Out-Null
& (Join-Path $fixture 'Setup.ps1') -Offline -SkipCredentials
$settingsPath=Join-Path $fixture 'config/settings.json'
$settings=Get-Content $settingsPath -Raw | ConvertFrom-Json
if ($settings.skipDangerousModePermissionPrompt) { throw 'Permission prompt bypass is enabled.' }
if ($settings.apiKeyHelper -notmatch '-File "') { throw 'Credential helper path is not quoted.' }
$keyPath=Join-Path $fixture 'gateway-key.dpapi'
$before=(Get-FileHash $keyPath).Hash
$settings.theme='fixture-preserved'
$settings | ConvertTo-Json -Depth 30 | Set-Content $settingsPath
& (Join-Path $fixture 'Setup.ps1') -Offline -SkipCredentials
if ((Get-FileHash $keyPath).Hash -ne $before) { throw 'Repeated setup replaced gateway credential.' }
if ((Get-Content $settingsPath -Raw | ConvertFrom-Json).theme -ne 'fixture-preserved') { throw 'Setup overwrote user settings.' }
$out=& powershell.exe -NoProfile -Command $settings.apiKeyHelper
if ($LASTEXITCODE -ne 0 -or -not $out) { throw 'Generated credential helper failed in path containing spaces.' }
$out=$null
$failed=$false
try { & (Join-Path $fixture 'Start-ReliabilityGateway.ps1') } catch { $failed=$_.Exception.Message -match 'Missing .* credential' }
if (-not $failed) { throw 'Missing provider credentials were not rejected.' }
foreach ($name in @('deepinfra','openrouter','outrun','unlid','allmodels')) {
    ConvertTo-SecureString 'synthetic-fixture' -AsPlainText -Force | ConvertFrom-SecureString | Set-Content (Join-Path $fixture "$name-key.dpapi")
}
$listener=[Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback,0)
$listener.Start()
try {
    $configPath=Join-Path $fixture 'gateway/config.json'
    $config=Get-Content $configPath -Raw | ConvertFrom-Json
    $config.listen_port=$listener.LocalEndpoint.Port
    $config | ConvertTo-Json -Depth 20 | Set-Content $configPath
    $failed=$false
    try { & (Join-Path $fixture 'Start-ReliabilityGateway.ps1') } catch { $failed=$_.Exception.Message -match 'occupied' }
    if (-not $failed) { throw 'An occupied port was not rejected.' }
} finally { $listener.Stop() }
Write-Host 'PASS: spaces, repeated setup, preserved settings, DPAPI helper, missing credentials, occupied port. Synthetic fixture retained in temporary storage.'
