#!/usr/bin/env bash
# Installation et vérification (Linux / macOS). Sous Windows : scripts/install.ps1
set -euo pipefail
command -v uv >/dev/null || { echo "Installez uv : https://docs.astral.sh/uv/"; exit 1; }
uv sync
if [ ! -f .env ]; then
  cp .env.example .env
  echo ".env créé : remplacez les mots de passe d'exemple."
fi
uv run pytest -q
