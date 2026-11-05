$ErrorActionPreference='Stop'
$dir=Join-Path $env:TEMP 'claude-deepseek-gitleaks-8.30.1'
New-Item -ItemType Directory -Force $dir | Out-Null
$zip=Join-Path $dir 'gitleaks.zip'
Invoke-WebRequest 'https://github.com/gitleaks/gitleaks/releases/download/v8.30.1/gitleaks_8.30.1_windows_x64.zip' -OutFile $zip
if ((Get-FileHash $zip -Algorithm SHA256).Hash -ne 'd29144deff3a68aa93ced33dddf84b7fdc26070add4aa0f4513094c8332afc4e') { throw 'Gitleaks checksum mismatch.' }
Expand-Archive $zip -DestinationPath $dir -Force
$env:PATH="$dir;$env:PATH"
