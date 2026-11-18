# Vélos — pipeline MLOps (UCI Bike Sharing)

Projet final 5MLDE : prédire le nombre de locations de vélos par heure (`cnt`, régression) avec une chaîne MLOps
complète : données, qualité bloquante, pipeline ML, MLflow, Prefect, Docker, API de prédiction, persistance des
traces, tests de charge et supervision. Les performances du modèle ne sont pas l'objectif : un `RandomForestRegressor` simple suffit.

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
Côté service : `API FastAPI (modèle chargé par l'alias champion) → PostgreSQL (schéma tracing : requêtes, prédictions,
feedbacks, versions, traitements) → Grafana (dashboard versionné)`, avec Locust pour les tests de charge.

**Synthèse des choix et résultats OPS** (décisions, preuves, charge, dimensionnement, limites) : `docs/SYNTHESE_OPS.md`.

## Installation
Prérequis : [uv](https://docs.astral.sh/uv/), Git, Docker Desktop.

```
# Windows (PowerShell)                       # Linux / macOS
powershell -ExecutionPolicy Bypass -File scripts\install.ps1      scripts/install.sh
```
Le script exécute `uv sync` (dépendances verrouillées par `uv.lock`), crée `.env` et lance les tests.
Puis ouvrez `.env` et choisissez vos 5 mots de passe (lettres et chiffres uniquement), **avant** le premier lancement
de Docker : PostgreSQL et Grafana les enregistrent au premier démarrage. Si le port 5432 est réservé
chez vous (cas observé sous Windows), mettez `POSTGRES_PORT=5433`.

```
docker compose --env-file .env -f infra/compose.yml up -d --build   # postgres, mlflow (7281), prefect (4200), grafana (3000), api (8000)
docker compose --env-file .env -f infra/compose.yml ps              # postgres, mlflow et api « healthy »
```
Ne lancez pas en plus un MLflow local sur le port 7281. Sous Windows, Docker Desktop exige la virtualisation (BIOS) et
WSL 2. Le dépôt force les fins de ligne LF des scripts `.sh` (`.gitattributes`) : sinon les rôles PostgreSQL ne sont pas créés.
Tant qu'aucun alias `champion` n'existe, l'API démarre et répond 503 : poser un champion (ci-dessous) puis
`curl.exe -X POST http://127.0.0.1:8000/model/reload`.

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

## Incident contrôlé n°2 : dépendance indisponible
| Panne | Comportement démontré |
|---|---|
| PostgreSQL arrêté | prédictions maintenues (200), `/health` = `degraded`, feedbacks en 503 `storage_unavailable`, cause journalisée ; retour automatique à la reprise |
| MLflow arrêté, API en service | `/model/reload` en 503, le modèle en mémoire continue d'être servi |
| MLflow arrêté, API qui (re)démarre | démarrage en < 30 s **sans modèle**, 503 `model_unavailable` ; `/model/reload` au retour du registre |
Commandes : `docs/DEMO.md` (section 6).

## Cycle de vie du modèle
- **Politique de qualification** `policies/qualification-v1.json` (version 1.1.0) : seuil absolu de MAE, gain minimal
  sur la baseline, garde-fou par segment météo, rechargement identique du modèle.
  Verdicts : `production-ready`, `rejected`, `pending` (preuve manquante), `demo-only` (données synthétiques).
- `velos select` **recalcule** la décision depuis les preuves du run (pas depuis un tag modifiable à la main) et refuse de
  remplacer le champion sans gain d'au moins 1 %. `velos rollback` rejoue `registry/selection-history.jsonl`.
- L'API charge le modèle **par alias** (`models:/velos-demand-cnt@champion`) au démarrage ; après `select` ou
  `rollback`, `POST /model/reload` sert la nouvelle version sans redéploiement. Chaque réponse indique la version utilisée.

## Service de prédiction, supervision et charge
| Sujet | Document | Points clés |
|---|---|---|
| API (`/predict`, `/feedback`, `/health`, `/model/reload`) | `docs/API.md`, `docs/CONTRAT_API.md` | validation du contrat (422), 503 sans modèle, 500 sans trace Python, version renvoyée |
| Persistance | `docs/API.md` | requêtes, durées, erreurs, prédictions, feedbacks et chargements dans `tracing.*` (rôle `api_writer`) |
| Supervision | `docs/SUPERVISION.md` | dashboard « Vélos - service et traitements » : usage, latence, erreurs, versions, feedbacks, traitements |
| Charge et dimensionnement | `docs/CHARGE.md`, `loadtests/results/summary.md` | 5 configurations mesurées ; **recommandation : 4 CPU / 4 processus / 2 Go pour 40 utilisateurs (p95 290 ms)** |
```
curl.exe -s -X POST http://127.0.0.1:8000/predict -H "Content-Type: application/json" --data-binary "@contract\example_request.json"
uv run python loadtests/run_config.py ; uv run python loadtests/summarize.py      # protocole de charge (~25 min)
```
Grafana : http://localhost:3000 (dossier *Velos*). Les flows journalisent leurs traitements : lancez-les avec
`uv run --env-file .env velos flow ...`.

## Tests
```
uv run pytest
```
105 tests (1 test d'intégration PostgreSQL lancé à la demande : voir `docs/API.md`). Couvrent : configuration, contrat d'entrée, ingestion et empreintes, chaque règle de qualité, absence de fuite de
données, publication atomique, qualification, sélection et retour arrière, chargement par alias, idempotence des lots,
quarantaine ; API (nominal, 422, 503, 500, feedback, panne de stockage, rechargement), stockage PostgreSQL, journal
des traitements, règle de recommandation de dimensionnement. La CI (`.github/workflows/ci.yml`) rejoue les tests, un
parcours de bout en bout sur données synthétiques, valide la configuration Compose et construit l'image de l'API.

## État de validation et limites connues
Validé sur Windows 11 avec Docker Desktop et les vraies données UCI : pipeline complet, suivi et registre MLflow,
qualification, alias, retour arrière, les services Docker, schéma SQL et droits des rôles, source Grafana, flows
Prefect (lot par lot et surveillance planifiée), incident de lot corrompu avec alerte. Reproduit sur une seconde machine
Windows 11 (`docs/TRANSFERT.md`), puis : API conteneurisée, persistance, cycle select → reload → rollback, tests de
charge, dashboard et incident « dépendance indisponible ».

Limites :
- Les nouvelles tentatives sur coupure réseau réelle ne sont testées qu'en simulation.
- L'alerte n'a été vérifiée qu'avec un récepteur HTTP de test (`{"text": ...}`) ; Teams peut exiger un autre format.
- Les seuils de qualification sont des valeurs de départ, ajustées une fois après le premier run (version 1.1.0) :
  ne jamais les déplacer pour faire passer un modèle, mais publier une nouvelle version de politique et requalifier.
- Les tests automatiques utilisent des données synthétiques (modèles `demo-only`) ; seuls les runs sur données UCI
  peuvent devenir champion.
- Les versions d'images Docker sont fixées mais `postgres:17` suit les mises à jour mineures.
- Une panne de PostgreSQL fait perdre les traces de la période (prédictions servies mais non enregistrées).
- `POST /model/reload` ne recharge que le processus qui reçoit la requête : avec plusieurs processus Uvicorn,
  redémarrer le conteneur `api`. L'endpoint n'est pas authentifié (usage local).
- Mesures de charge sur une seule machine (Locust, API et base partagent le CPU), une série par configuration.
- Liste complète et justifications : `docs/SYNTHESE_OPS.md`.
