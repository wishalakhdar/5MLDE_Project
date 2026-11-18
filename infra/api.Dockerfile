# Image de l'API de prédiction. Construite par infra/compose.yml (contexte = racine du projet).
# Dépendances installées depuis uv.lock (--frozen) : mêmes versions que sur le poste et dans la CI.
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.15 /uv /bin/uv

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1

# 1) Dépendances seules (couche mise en cache tant que pyproject.toml / uv.lock ne changent pas)
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

# 2) Code du projet et configuration
COPY src ./src
RUN uv sync --frozen --no-dev
COPY config.json ./

ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8000

# Nombre de processus Uvicorn réglable (API_WORKERS) : paramètre comparé pendant les tests de charge.
# Chaque processus charge son propre exemplaire du modèle et ouvre son propre pool de connexions.
CMD ["sh", "-c", "exec uvicorn velos.api.app:create_app --factory --host 0.0.0.0 --port 8000 --workers ${API_WORKERS:-1}"]
