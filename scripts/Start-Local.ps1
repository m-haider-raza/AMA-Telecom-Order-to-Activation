[CmdletBinding()]
param([int]$Port=8000)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
$python = Join-Path (Get-Location) '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw "Create .venv and install .[dev] first; see README." }
$rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
function New-Token {
    $bytes = New-Object byte[] 36
    $rng.GetBytes($bytes)
    return [Convert]::ToBase64String($bytes).Replace('+','-').Replace('/','_').TrimEnd('=')
}
try {
    $env:AZURE_STORAGE_ACCOUNT_URL = $null
    $env:AZURE_OPENAI_ENDPOINT = $null
    $env:AZURE_OPENAI_DEPLOYMENT = $null
    $env:DEMO_ADMIN_TOKEN = New-Token
    $env:DEMO_PARTNER_TOKEN = New-Token
    @{adminToken=$env:DEMO_ADMIN_TOKEN; partnerToken=$env:DEMO_PARTNER_TOKEN; demoUrl="http://localhost:$Port"} |
        ConvertTo-Json | Set-Content -LiteralPath 'demo-credentials.local.json' -Encoding UTF8
    Write-Output "Open http://localhost:$Port. Use Demo access with a token from demo-credentials.local.json."
    & $python -m uvicorn app.main:app --host 127.0.0.1 --port $Port --workers 1
    if ($LASTEXITCODE -ne 0) { throw 'Application exited with an error.' }
} finally { $rng.Dispose() }
