# API de prédiction

## Lancer
Prérequis : services Docker lancés, alias `champion` posé (`uv run velos select ...`).
```
uv run --env-file .env uvicorn velos.api.app:create_app --factory --host 127.0.0.1 --port 8000
```
`--env-file .env` transmet à l'API le mot de passe `API_WRITER_PASSWORD` et le port `POSTGRES_PORT`
(le fichier `.env` n'est jamais versionné). Documentation interactive : http://127.0.0.1:8000/docs

## Persistance (PostgreSQL)
`config.json` → `api.trace_store` : `postgres` (traces dans le schéma `tracing`) ou `memory` (essais sans base).
Réglages dans `api.postgres` ; l'environnement peut surcharger l'hôte (`VELOS_DB_HOST`) et le port (`POSTGRES_PORT`).
L'API se connecte avec le rôle `api_writer` (INSERT/SELECT, pas de DELETE) via un pool de connexions
(`pool_max` : paramètre comparé lors des tests de charge).

Vérifier les traces :
```
docker compose --env-file .env -f infra/compose.yml exec postgres psql -U postgres -d velos -c "SELECT endpoint, status_code, round(latency_ms::numeric,1) AS ms, model_version, error_code FROM tracing.api_requests ORDER BY received_at DESC LIMIT 5"
```
Test d'intégration sur la vraie base : `$env:VELOS_PG_TESTS="1"; uv run --env-file .env pytest tests/test_postgres_store.py`

## Routes
| Route | Rôle | Codes |
|---|---|---|
| `POST /predict` | Prédictions en ligne ou par lot (`instances` : 1 à `api.max_instances` lignes) | 200, 422, 503, 500 |
| `POST /feedback` | Valeur observée pour une prédiction (`prediction_id`, `observed_cnt` ≥ 0) | 201, 404, 422, 503 |
| `GET /health` | État du service : modèle chargé (version) et base de traces | 200, 503 |
| `POST /model/reload` | Recharge le modèle désigné par l'alias, sans redémarrer | 200, 503 |

Chaque réponse contient un `request_id` (aussi dans l'en-tête `X-Request-ID`) ; `/predict` renvoie
la version du modèle utilisée. Les erreurs suivent `contract/error_example.json`, sans trace Python.

## Choix
- **Chargement par alias, une seule fois au démarrage** (`models:/velos-demand-cnt@champion`) : un changement
  de champion ne demande aucune modification de code ; `POST /model/reload` le prend en compte sans redéploiement.
  Si le rechargement échoue, le modèle en service est conservé.
- **Sans modèle, l'API démarre quand même** et répond 503 : `/health` permet de diagnostiquer la panne.
- **Validation en deux temps** : Pydantic vérifie la forme du JSON, puis `velos.contract.validate_inference_frame`
  (contrat du modèle) vérifie les valeurs. Les règles métier ne sont écrites qu'une fois.
- **Traçage de chaque requête** (`/predict`, `/feedback`, `/model/reload`) : identifiant, route, statut, durée,
  version du modèle, code d'erreur, et les prédictions avec leurs features.
- **Une panne de la base de traces ne bloque pas les prédictions** : la trace perdue est journalisée et `/health`
  passe en `degraded` ; les feedbacks, eux, répondent 503 (ils n'ont de sens qu'enregistrés).
- Limite : `POST /model/reload` n'est pas authentifié (service local uniquement).
