#!/usr/bin/env bash
set -euo pipefail
command -v uv >/dev/null || { echo "Installez uv : https://docs.astral.sh/uv/"; exit 1; }
uv sync
[ -f .env ] || cp .env.example .env && echo ".env créé : remplacez les mots de passe d'exemple."
uv run pytest -q
