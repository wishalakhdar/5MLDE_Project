# Script de démonstration (nominal, incidents n°1 et n°2, cycle de mise en service, charge)

À rejouer **sur le MLflow Docker** (http://localhost:7281). Commandes PowerShell, depuis la racine du projet.
Durée : environ 10 minutes. Chaque étape indique le résultat attendu.

## 0. Préparation (une fois)
```
docker compose --env-file .env -f infra/compose.yml up -d
docker compose --env-file .env -f infra/compose.yml ps          # 5 services running, postgres, mlflow et api healthy
$env:PREFECT_API_URL="http://localhost:4200/api"
```
**Si vous avez déjà fait des essais avec un autre MLflow** (par exemple un MLflow local), renommez l'ancien historique
des sélections, sinon `select` et `rollback` refusent volontairement de travailler (incohérence historique / registre) :
```
Rename-Item registry\selection-history.jsonl selection-history.ancien.jsonl
```

## 1. Fonctionnement nominal
```
uv run velos versions                                   # liste les versions existantes
uv run velos replay-next                                # dépose le prochain lot
uv run velos flow --lot data/incoming/<lot>.csv         # flow Prefect : 6 tâches vertes, nouvelle version
uv run velos versions                                   # verdict production-ready, MAE de validation
```
Montrer dans le navigateur : Prefect (http://localhost:4200, Runs) et MLflow (Models, velos-demand-cnt).

## 2. Mise en service par alias et retour arrière
Choisir deux versions `production-ready` dont la seconde a une MAE de validation **au moins 1 % plus basse** que la
première (lisible avec `uv run velos versions`).
```
uv run velos select --version <A> --reason "premier champion"
uv run velos select --version <B> --reason "meilleure MAE"        # accepté : gain suffisant
uv run velos predict --alias champion --input data/store/qualification.csv --output reports/predictions.csv
                                                                  # affiche models:/velos-demand-cnt@champion (version B)
uv run velos rollback                                             # l'alias revient sur A
uv run velos predict --alias champion --input data/store/qualification.csv --output reports/predictions.csv
                                                                  # affiche la version A
```
Contre-exemple utile : `uv run velos select --version <C> ...` avec une version moins bonne est **refusé** (gain < 1 %).

## 3. Incident n°1 : lot invalide
```
$env:ALERT_WEBHOOK_URL="<adresse du récepteur d'alertes>"      # optionnel
uv run velos make-bad-lot --kind negatif
uv run velos flow --lot data/incoming/<lot>.csv
```
Attendu : message « LOT REJETÉ », flow `Failed` dans Prefect, fichier + `*.rapport.json` dans `data\rejected`,
run MLflow du lot en `Failed`, **aucune** nouvelle version, une alerte reçue.
Puis reprise normale : `uv run velos replay-next` et `uv run velos flow --lot data/incoming/<lot>.csv` (le lot valide est accepté).

## 4. Service de prédiction : du champion à l'API, puis retour arrière
L'API tourne dans Docker (`infra-api-1`). La variable `$P` (définie une fois par terminal) sert aux sections 4 à 6.
```
$P = "@contract\example_request.json"
curl.exe -s http://127.0.0.1:8000/health                                    # version servie (ex. 1)
uv run velos versions                                                       # v1 64.95 | v2 68.57 | v3 61.07 (MAE)
uv run velos select --version 3 --actor <nom> --reason "MAE meilleure"      # accepté : +6 % sur v1
uv run velos select --version 2 --actor <nom> --reason "test"               # REFUSÉ : gain -12.3 % < 1 %
curl.exe -s http://127.0.0.1:8000/health                                    # toujours v1 : rien n'est rechargé en douce
curl.exe -s -X POST http://127.0.0.1:8000/model/reload                      # version 3, previous_version 1
curl.exe -s -X POST http://127.0.0.1:8000/predict -H "Content-Type: application/json" --data-binary $P   # "version": 3
uv run locust -f loadtests/locustfile.py --host http://127.0.0.1:8000 --headless -u 5 -r 5 --run-time 60s --only-summary
uv run velos rollback --actor <nom>                                         # retour sur v1
curl.exe -s -X POST http://127.0.0.1:8000/model/reload                      # version 1, previous_version 3
Get-Content -Encoding UTF8 registry\selection-history.jsonl                 # select, select, rollback (qui, quand, pourquoi)
```
Grafana (« Last 30 minutes ») : *Prédictions par version* (v1 → v3 → v1) et *Chargements du modèle par l'API*.

## 5. Supervision et traitements
```
uv run --env-file .env velos replay-next
uv run --env-file .env velos flow --lot data/incoming/<lot>.csv            # traitement « succeeded » + nouvelle version
uv run --env-file .env velos make-bad-lot --kind negatif
uv run --env-file .env velos flow --lot data/incoming/<lot>.csv            # traitement « rejected »
```
Dashboard « Vélos - service et traitements » (http://localhost:3000, dossier Velos) : rangées service, modèle et
traitements du pipeline (tableau des derniers traitements avec statut, durée, version, verdict).

## 6. Incident n°2 : dépendance indisponible
**PostgreSQL arrêté**
```
docker compose --env-file .env -f infra/compose.yml stop postgres
curl.exe -s http://127.0.0.1:8000/health                                   # "status":"degraded", "database":"unavailable"
curl.exe -s -w "`nHTTP %{http_code}`n" -X POST http://127.0.0.1:8000/predict -H "Content-Type: application/json" --data-binary $P   # 200
curl.exe -s -w "`nHTTP %{http_code}`n" -X POST http://127.0.0.1:8000/feedback -H "Content-Type: application/json" --data-binary "@reports\feedback.json"   # 503 storage_unavailable
docker compose --env-file .env -f infra/compose.yml logs api --tail 8      # « Trace perdue ... AdminShutdown / PoolTimeout »
docker compose --env-file .env -f infra/compose.yml start postgres          # ~15 s plus tard : /health ok, feedback 201
```
(`reports\feedback.json` : `{"prediction_id": "<id d'une prédiction faite avant la panne>", "observed_cnt": 400}`.)

**MLflow arrêté**
```
docker compose --env-file .env -f infra/compose.yml stop mlflow
curl.exe -s -X POST http://127.0.0.1:8000/model/reload                     # 503 : « le modèle actuel est conservé »
docker compose --env-file .env -f infra/compose.yml restart api            # ~30 s : « Démarrage SANS modèle »
curl.exe -s -w "`nHTTP %{http_code}`n" http://127.0.0.1:8000/health         # 503 "unavailable"
curl.exe -s -w "`nHTTP %{http_code}`n" -X POST http://127.0.0.1:8000/predict -H "Content-Type: application/json" --data-binary $P   # 503 model_unavailable
docker compose --env-file .env -f infra/compose.yml start mlflow           # attendre ~45 s (healthy)
curl.exe -s -X POST http://127.0.0.1:8000/model/reload                     # version 1, previous_version null
```
Grafana : *Erreurs par code* montre les `model_unavailable` de la panne MLflow. La panne PostgreSQL, elle, n'y
apparaît **pas** : les requêtes de cette période (dont les 503 `storage_unavailable`) n'ont pas pu être enregistrées,
puisque la base de traces était justement arrêtée. Elle se voit dans les logs de l'API et par un trou dans les courbes.
Constat lors de la première exécution : sans borne sur les reprises du client MLflow, l'API restait **injoignable
plusieurs minutes** au démarrage ; corrigé par `MLFLOW_HTTP_REQUEST_MAX_RETRIES=2` dans `infra/compose.yml`.

## 7. Charge et dimensionnement
```
uv run python loadtests/run_config.py --only B-1cpu-1w      # ~5 min (toutes les configurations : ~25 min)
uv run python loadtests/summarize.py
```
Laisser la machine sans autre activité et sans mise en veille pendant la mesure (une mise en veille a invalidé un palier
lors de la première série). Résultats et recommandation : `loadtests/results/summary.md`, `docs/CHARGE.md`.

## 8. Preuves à conserver pour la synthèse OPS
- Capture MLflow : liste des versions avec tags de qualification, alias `champion`.
- `type registry\selection-history.jsonl` (select, select, rollback).
- Rapport de rejet `data\rejected\*.rapport.json` et capture du run Prefect en échec
  (conservés : `docs/preuves/rapport-rejet.png`, `docs/preuves/rejet-et-alerte-terminal.png`).
- Capture de l'alerte : sans `ALERT_WEBHOOK_URL`, l'alerte est écrite dans le journal (`ALERTE (aucun webhook
  configuré)`), visible sur `docs/preuves/rejet-et-alerte-terminal.png` ; avec un récepteur, capturer le message reçu.
- Captures Grafana (`docs/preuves/`) : prédictions par version (v1 → v3 → v1), chargements du modèle, erreurs par code
  pendant l'incident n°2, derniers traitements ; `loadtests/results/summary.md`.
