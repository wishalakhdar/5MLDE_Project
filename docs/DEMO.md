# Script de démonstration (nominal + incident n°1 + retour arrière)

À rejouer **sur le MLflow Docker** (http://localhost:7281). Commandes PowerShell, depuis la racine du projet.
Durée : environ 10 minutes. Chaque étape indique le résultat attendu.

## 0. Préparation (une fois)
```
docker compose --env-file .env -f infra/compose.yml up -d
docker compose --env-file .env -f infra/compose.yml ps          # 4 services running, postgres healthy
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

## 4. Preuves à conserver pour la synthèse OPS
- Capture MLflow : liste des versions avec tags de qualification, alias `champion`.
- `type registry\selection-history.jsonl` (select, select, rollback).
- Rapport de rejet `data\rejected\*.rapport.json` et capture du run Prefect en échec.
- Capture de l'alerte reçue.
