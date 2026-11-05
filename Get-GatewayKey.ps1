# Claude Code apiKeyHelper. stdout must contain only the local gateway key.
$ErrorActionPreference = 'Stop'
# The helper can be spawned by a PowerShell 7 host or a background agent.
# Use Windows PowerShell's modules for its DPAPI SecureString commands.
$env:PSModulePath = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\Modules;$env:ProgramFiles\WindowsPowerShell\Modules;$env:USERPROFILE\Documents\WindowsPowerShell\Modules"
try {
    $encrypted = (Get-Content -LiteralPath (Join-Path $PSScriptRoot 'gateway-key.dpapi') -Raw).Trim()
    $secureKey = $encrypted | ConvertTo-SecureString
    $gatewayCredential = [PSCredential]::new('local-gateway', $secureKey)
    [Console]::Out.WriteLine($gatewayCredential.GetNetworkCredential().Password)
} catch {
    [Console]::Error.WriteLine('Cannot read the protected local gateway credential for this Windows account.')
    exit 1
} finally {
    $encrypted = $null
    $secureKey = $null
    $gatewayCredential = $null
}
