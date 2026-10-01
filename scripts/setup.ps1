$ErrorActionPreference = "Stop"
$projectDir = Split-Path -Parent $PSScriptRoot

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    irm https://astral.sh/uv/install.ps1 | iex
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}

Set-Location $projectDir
$env:UV_CACHE_DIR = "$projectDir\.uv-cache"
uv sync --locked --extra dev
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$env:PLAYWRIGHT_BROWSERS_PATH = "$projectDir\state\main\playwright-browsers"
uv run --locked playwright install chromium
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
uv run --locked hhraiser setup
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
uv run --locked python -m hh_raiser.setup_ollama
exit $LASTEXITCODE
