$ErrorActionPreference = 'Stop'
try {
    $env:CLAUDE_CONFIG_DIR = Join-Path $PSScriptRoot 'config'
    $settingsPath = Join-Path $env:CLAUDE_CONFIG_DIR 'settings.json'
    $settings = Get-Content -LiteralPath $settingsPath -Raw | ConvertFrom-Json
    $env:CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS = $null
    foreach ($property in $settings.env.PSObject.Properties) {
        [Environment]::SetEnvironmentVariable($property.Name, [string]$property.Value, 'Process')
    }
    foreach ($name in @('ANTHROPIC_MODEL', 'ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN', 'CLAUDE_CODE_OAUTH_TOKEN', 'CLAUDE_CODE_USE_BEDROCK', 'CLAUDE_CODE_USE_VERTEX', 'CLAUDE_CODE_USE_FOUNDRY')) {
        [Environment]::SetEnvironmentVariable($name, $null, 'Process')
    }
    # Separate routing status for each launcher instance, including its subagents.
    $env:CLAUDE_DEEPSEEK_RUN_ID = [Guid]::NewGuid().ToString('N')
    $customHeaders = @($env:ANTHROPIC_CUSTOM_HEADERS -split "`n" | Where-Object { $_ -and $_ -notmatch '^\s*X-Claude-Deepseek-Run\s*:' })
    $customHeaders += 'X-Claude-Deepseek-Run: ' + $env:CLAUDE_DEEPSEEK_RUN_ID
    $env:ANTHROPIC_CUSTOM_HEADERS = $customHeaders -join "`n"
    & (Join-Path $PSScriptRoot 'Start-ReliabilityGateway.ps1')
    # Profile apiKeyHelper supplies the DPAPI-protected gateway credential on
    # demand, including after the interactive agent view rebuilds its environment.
    $claude = (Get-Command claude.exe -ErrorAction Stop).Source
    & $claude --settings $settingsPath --mcp-config (Join-Path $PSScriptRoot "config/mcp.json") @args
    $result = $LASTEXITCODE
} catch {
    [Console]::Error.WriteLine(('claude-deepseek: ' + $_.Exception.Message) -replace 'sk-or-v1-[a-zA-Z0-9]+', '[redacted]')
    $result = 1
} finally {
    $env:ANTHROPIC_AUTH_TOKEN = $null
    $env:ANTHROPIC_API_KEY = $null
}
exit $result
