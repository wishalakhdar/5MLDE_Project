# Supervision (Grafana)

Dashboard **« Vélos - service et traitements »** (dossier *Velos*), versionné dans
`infra/grafana/provisioning/dashboards/json/velos-service.json` et chargé automatiquement au démarrage de Grafana.
Il lit uniquement PostgreSQL (schéma `tracing`) avec le rôle en lecture seule `grafana_reader` :
aucun Prometheus, une seule source de vérité pour les traces d'usage et de traitement.

| Rangée | Panneaux | Source |
|---|---|---|
| Service : utilisation et performances | requêtes, latence p95, % d'erreurs 5xx, % de refus 4xx, version servie, feedbacks ; requêtes/minute par résultat ; latence p50/p95/p99 ; erreurs par code ; prédictions par version | `api_requests`, `predictions`, `model_versions`, `feedbacks` (écrits par l'API) |
| Modèle : prédictions et retours | distribution des prédictions ; écart moyen prédiction / valeur observée ; historique des chargements du modèle | `predictions`, `feedbacks`, `model_versions` |
| Traitements du pipeline | traitements par statut ; derniers traitements (lot, statut, durée, version, verdict, erreur) | `pipeline_runs` (écrit par le flow Prefect) |

Les lignes du test d'intégration (`/test-integration`, version 0) sont exclues des panneaux.

## Traçage des traitements
À la fin de chaque exécution de `velos-process-lot` (succès, lot rejeté, panne ou lot déjà traité), le flow
ajoute une ligne dans `tracing.pipeline_runs` avec le rôle dédié `pipeline_writer` (INSERT/SELECT sur cette
seule table). Ce suivi est « au mieux » : si PostgreSQL est indisponible, le traitement n'échoue pas pour autant
(avertissement dans les logs). Lancer les flows avec le fichier `.env` pour fournir le mot de passe :
```
$env:PREFECT_API_URL="http://localhost:4200/api"
uv run --env-file .env velos flow --lot data/incoming/<lot>.csv
```

## Mise en place sur une installation existante
Les scripts de `infra/postgres/` ne s'exécutent qu'à la création du volume. Sur une base déjà initialisée :
1. Ajouter `PIPELINE_WRITER_PASSWORD=<mot de passe>` dans `.env` (lettres et chiffres).
2. Recréer les conteneurs (les volumes et donc les données sont conservés) puis créer la table et le rôle :
```
docker compose --env-file .env -f infra/compose.yml up -d
docker compose --env-file .env -f infra/compose.yml exec postgres bash /docker-entrypoint-initdb.d/20-pipeline-runs.sh
docker compose --env-file .env -f infra/compose.yml restart grafana
```
Le script est idempotent : le relancer ne crée pas de doublon.
