# Installation et verification (Windows PowerShell). Lancer depuis la racine du projet :
#   powershell -ExecutionPolicy Bypass -File scripts\install.ps1
$ErrorActionPreference = "Stop"
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "Installez uv : https://docs.astral.sh/uv/ puis rouvrez PowerShell."
    exit 1
}
uv sync
if (-not (Test-Path .env)) {
    Copy-Item .env.example .env
    Write-Host ".env cree : choisissez vos mots de passe (lettres et chiffres) avant de lancer Docker."
}
uv run pytest -q
