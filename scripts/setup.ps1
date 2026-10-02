$ErrorActionPreference = "Stop"
$projectDir = Split-Path -Parent $PSScriptRoot

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    irm https://astral.sh/uv/install.ps1 | iex
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}

Set-Location $projectDir
$stateDir = Join-Path $projectDir "state\main"
New-Item -ItemType Directory -Path $stateDir -Force | Out-Null
if (-not (Test-Path -LiteralPath "$projectDir\.env")) {
    Copy-Item -LiteralPath "$projectDir\.env.example" -Destination "$projectDir\.env"
}
if (-not (Test-Path -LiteralPath "$stateDir\hh-config.ini")) {
    Copy-Item -LiteralPath "$projectDir\hh-config.example.ini" -Destination "$stateDir\hh-config.ini"
}
$env:UV_CACHE_DIR = "$projectDir\.uv-cache"
uv sync --locked --extra dev
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
uv run --locked playwright install chromium
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host "Установка готова. Заполните .env и state/main/hh-config.ini, затем запустите task full-activity."
