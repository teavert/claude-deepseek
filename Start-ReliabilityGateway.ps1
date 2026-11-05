$ErrorActionPreference = 'Stop'
$gatewayRoot = Join-Path $PSScriptRoot 'gateway'
$config = Get-Content -LiteralPath (Join-Path $gatewayRoot 'config.json') -Raw | ConvertFrom-Json
if ($config.listen_host -ne '127.0.0.1') { throw 'The reliability gateway must bind to 127.0.0.1.' }
foreach ($name in @('gateway','deepinfra','openrouter','outrun','unlid','allmodels')) {
    if (-not (Test-Path (Join-Path $PSScriptRoot "$name-key.dpapi"))) { throw "Missing $name credential. Run Setup.ps1." }
}
$secure = (Get-Content -LiteralPath (Join-Path $PSScriptRoot 'gateway-key.dpapi') -Raw).Trim() | ConvertTo-SecureString
$token = ([PSCredential]::new('local', $secure)).GetNetworkCredential().Password
$headers = @{ Authorization = 'Bearer ' + $token }
$healthUrl = 'http://127.0.0.1:' + $config.listen_port + '/health'
$mutex = [Threading.Mutex]::new($false, 'Local\ClaudeDeepSeekReliabilityStartup')
$locked = $false
$configHash = & (Join-Path $gatewayRoot 'venv\Scripts\python.exe') (Join-Path $gatewayRoot 'config_fingerprint.py') (Join-Path $gatewayRoot 'config.json')
if ($LASTEXITCODE -ne 0) { throw 'Cannot fingerprint the gateway configuration.' }
function Test-Gateway {
    try {
        $health = Invoke-RestMethod -Uri $healthUrl -Headers $headers -TimeoutSec 2
        if ($health.gateway_revision -ne 8 -or $health.config_sha256 -ne $configHash) { return $false }
        if ($health.primary_service_tier -ne $config.primary_service_tier) { return $false }
        foreach ($name in @('primary_reasoning_effort', 'primary_temperature', 'primary_top_p')) {
            if ($health.$name -ne $config.$name) { return $false }
        }
        return ($health.service -eq 'claude-deepseek-reliability-v1' -and $health.primary -eq $config.primary_model -and $health.fallback -eq $config.fallback_model -and $health.support -eq $config.support_model -and (($health.fallback_chain | ConvertTo-Json -Depth 5 -Compress) -eq ($config.fallback_chain | ConvertTo-Json -Depth 5 -Compress)))
    } catch { return $false }
}
try {
    if (Test-Gateway) { return }
    try { $locked = $mutex.WaitOne(45000) } catch [Threading.AbandonedMutexException] { $locked = $true }
    if (-not $locked) { throw 'Another launcher is still starting the reliability gateway. Try again shortly.' }
    if (Test-Gateway) { return }
    $listeners = [Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties().GetActiveTcpListeners()
    if ($listeners | Where-Object { $_.Port -eq $config.listen_port }) {
        throw ('Port ' + $config.listen_port + ' is occupied by a service that did not pass the gateway health check.')
    }
    $python = Join-Path $gatewayRoot 'venv\Scripts\python.exe'
    $server = Join-Path $gatewayRoot 'server.py'
    $process = Start-Process -FilePath $python -ArgumentList @('-E', '-s', ('"' + $server + '"')) -WorkingDirectory $gatewayRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $gatewayRoot ('stdout-' + $config.listen_port + '.log')) -RedirectStandardError (Join-Path $gatewayRoot ('stderr-' + $config.listen_port + '.log'))
    for ($i = 0; $i -lt 45; $i++) {
        if (Test-Gateway) { return }
        $process.Refresh()
        if ($process.HasExited) { throw 'The reliability gateway exited during startup. Check the gateway stderr log for the configured port.' }
        Start-Sleep -Milliseconds 500
    }
    throw 'The reliability gateway did not become ready. Check the gateway stderr log for the configured port.'
} finally {
    if ($locked) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
    $token = $null
    $headers = $null
    $secure = $null
}
