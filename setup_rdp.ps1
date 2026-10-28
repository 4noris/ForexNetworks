param(
    [int]$PublicPort = 8780,
    [string]$PublicIp = "192.0.2.10"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

if (-not (Test-Path -LiteralPath ".env")) {
    throw "Create .env from .env.example and add the MT5 and Telegram credentials first."
}

$python = Get-Command py -ErrorAction SilentlyContinue
if ($python) {
    & py -3.11 -m venv .venv
    if ($LASTEXITCODE -ne 0) { & py -3 -m venv .venv }
} else {
    & python -m venv .venv
}

& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt

try {
    & .\.venv\Scripts\python.exe -c "import MT5Manager; print('MT5 Manager SDK: ready')"
} catch {
    throw "MT5Manager is not installed for this Python environment. Install the broker/vendor MT5 Manager SDK package, then rerun this script."
}

if (-not (Get-Command npm.cmd -ErrorAction SilentlyContinue)) {
    throw "Node.js LTS is required. Install it from nodejs.org, then rerun this script."
}

Push-Location frontend
try {
    & npm.cmd ci
    & npm.cmd run build
} finally {
    Pop-Location
}

function Set-EnvValue([string]$Name, [string]$Value) {
    $path = Join-Path $Root ".env"
    $lines = @(Get-Content -LiteralPath $path -Encoding UTF8)
    $replacement = "$Name=$Value"
    $found = $false
    $updated = foreach ($line in $lines) {
        if ($line -match "^$([regex]::Escape($Name))=") {
            $found = $true
            $replacement
        } else {
            $line
        }
    }
    if (-not $found) { $updated += $replacement }
    Set-Content -LiteralPath $path -Value $updated -Encoding UTF8
}

Set-EnvValue "PORT" $PublicPort
Set-EnvValue "ALLOWED_ORIGINS" "http://${PublicIp}:$PublicPort"

$ruleName = "MT5 Brokerage TCP $PublicPort"
if (-not (Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -DisplayName $ruleName -Direction Inbound -Action Allow -Protocol TCP -LocalPort $PublicPort | Out-Null
}

Write-Host "Setup complete." -ForegroundColor Green
Write-Host "Start with: .\start_production.cmd"
Write-Host "Open: http://${PublicIp}:$PublicPort"
Write-Host "Also allow TCP $PublicPort in the cloud firewall/security group."
