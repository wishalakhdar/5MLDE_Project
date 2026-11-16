# Vélos — pipeline MLOps (UCI Bike Sharing)

Projet final 5MLDE : prédire le nombre de locations de vélos par heure (`cnt`, régression) avec une chaîne MLOps
complète : données, qualité bloquante, pipeline ML, MLflow, Prefect, Docker, puis (phase 2) API, persistance,
charge et supervision. Les performances du modèle ne sont pas l'objectif : un `RandomForestRegressor` simple suffit.

## Données
- **Source** : [UCI Bike Sharing](https://archive.ics.uci.edu/dataset/275/bike+sharing+dataset), fichier `hour.csv`
  (17 379 lignes, 2011-2012). **Licence CC BY 4.0**, DOI 10.24432/C5W894. Citation et détails : `data/PROVENANCE.md`.
- **Rejeu par lots (à préciser, comme l'exige le sujet)** : le jeu est historique, il est rejoué par lots mensuels pour
  simuler l'arrivée de nouvelles données. Base d'entraînement = 2011 ; lots = 2012-01 à 2012-11 ; **corpus de
  qualification fixe** = 2012-12 (jamais utilisé pour entraîner : il sert à comparer les versions sur le même examen).
- **Reproductibilité** : `velos fetch` télécharge l'archive et vérifie l'empreinte SHA-256 inscrite dans
  `data/manifest.json` (versionné). Un fichier d'empreinte différente est refusé.

## Architecture
Voir `docs/ARCHITECTURE.md`. En bref :
`fetch → replay-init → [ingestion + qualité bloquante → prétraitement → entraînement → évaluation] → registre MLflow
(qualification, alias champion, retour arrière)`, le tout orchestré par Prefect (un lot déclenche un flow, un échec
déclenche une alerte). Une anomalie de qualité arrête tout : le lot est mis en quarantaine (`data/rejected`) avec un rapport.

## Installation
Prérequis : [uv](https://docs.astral.sh/uv/), Git, Docker Desktop.

```
# Windows (PowerShell)                       # Linux / macOS
powershell -ExecutionPolicy Bypass -File scripts\install.ps1      scripts/install.sh
```
Le script exécute `uv sync` (dépendances verrouillées par `uv.lock`), crée `.env` et lance les tests.
Puis ouvrez `.env` et choisissez vos mots de passe (lettres et chiffres uniquement). Si le port 5432 est réservé
chez vous (cas observé sous Windows), mettez `POSTGRES_PORT=5433`.

```
docker compose --env-file .env -f infra/compose.yml up -d     # postgres, mlflow (7281), prefect (4200), grafana (3000)
docker compose --env-file .env -f infra/compose.yml ps        # 4 services « running », postgres « healthy »
```
Ne lancez pas en plus un MLflow local sur le port 7281.

## Reproduire le fonctionnement nominal
```
uv run velos fetch                       # données UCI + empreinte
uv run velos replay-init                 # base / lots / corpus de qualification
uv run velos run                         # entraînement de base, suivi MLflow, enregistrement et qualification
uv run velos versions                    # versions du registre : verdict, métrique, alias
uv run velos select --version 1 --reason "premier champion"
uv run velos replay-next                 # dépose le lot suivant dans data/incoming
$env:PREFECT_API_URL="http://localhost:4200/api"       # (Linux : export PREFECT_API_URL=...)
uv run velos flow --lot data/incoming/lot-2012-01.csv  # un lot via Prefect
uv run velos select --version 2 --reason "..."         # refusé si le gain est < 1 %
uv run velos rollback                    # retour au champion précédent
uv run velos predict --alias champion --input data/store/qualification.csv --output reports/predictions.csv
uv run velos serve-flows                 # surveillance planifiée de data/incoming (toutes les 10 min)
```
Script de démonstration complet (nominal + incident + retour arrière) : `docs/DEMO.md`.

## Incident contrôlé n°1 : lot invalide
```
uv run velos make-bad-lot --kind negatif       # ou colonne | volume
uv run velos flow --lot data/incoming/lot-2012-0X.csv
```
Résultat attendu : code de sortie 2 (`velos run`) ou flow `Failed`, lot déplacé dans `data/rejected` avec
`*.rapport.json`, aucun nouveau modèle, run MLflow en `FAILED`, une alerte (webhook si `ALERT_WEBHOOK_URL` est défini).

## Cycle de vie du modèle
- **Politique de qualification** `policies/qualification-v1.json` (version 1.1.0) : seuil absolu de MAE, gain minimal
  sur la baseline, garde-fou par segment météo, rechargement identique du modèle.
  Verdicts : `production-ready`, `rejected`, `pending` (preuve manquante), `demo-only` (données synthétiques).
- `velos select` **recalcule** la décision depuis les preuves du run (pas depuis un tag modifiable à la main) et refuse de
  remplacer le champion sans gain d'au moins 1 %. `velos rollback` rejoue `registry/selection-history.jsonl`.
- L'API doit charger le modèle **par alias** : `models:/velos-demand-cnt@champion` (voir `docs/CONTRAT_API.md`).

## Tests
```
uv run pytest -q
```
Couvrent : configuration, contrat d'entrée, ingestion et empreintes, chaque règle de qualité, absence de fuite de
données, publication atomique, qualification, sélection et retour arrière, chargement par alias, idempotence des lots,
quarantaine. La CI (`.github/workflows/ci.yml`) rejoue les tests et un parcours de bout en bout sur données synthétiques.

## État de validation et limites connues
Validé sur Windows 11 avec Docker Desktop et les vraies données UCI : pipeline complet, suivi et registre MLflow,
qualification, alias, retour arrière, les 4 services Docker, schéma SQL et droits des rôles, source Grafana, flows
Prefect (lot par lot et surveillance planifiée), incident de lot corrompu avec alerte.

Limites :
- Les nouvelles tentatives sur coupure réseau réelle ne sont testées qu'en simulation.
- L'alerte n'a été vérifiée qu'avec un récepteur HTTP de test (`{"text": ...}`) ; Teams peut exiger un autre format.
- Les seuils de qualification sont des valeurs de départ, ajustées une fois après le premier run (version 1.1.0) :
  ne jamais les déplacer pour faire passer un modèle, mais publier une nouvelle version de politique et requalifier.
- Les tests automatiques utilisent des données synthétiques (modèles `demo-only`) ; seuls les runs sur données UCI
  peuvent devenir champion.
- Les versions d'images Docker sont fixées mais `postgres:17` suit les mises à jour mineures.
- Phase 2 (API, persistance, Locust, Grafana, dimensionnement) : à réaliser, voir `docs/CONTRAT_API.md`.
