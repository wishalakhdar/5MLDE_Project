# Vélos — pipeline MLOps (UCI Bike Sharing)

Sujet : prédire le nombre de locations horaires (`cnt`, régression). Données : UCI Bike Sharing `hour.csv`, CC BY 4.0, DOI 10.24432/C5W894 (voir `data/PROVENANCE.md`). Architecture : `docs/ARCHITECTURE.md`.

## Installation
```
scripts/install.sh          # uv sync + tests
cp .env.example .env        # puis changer les mots de passe
docker compose --env-file .env -f infra/compose.yml up -d
```
## Reproduction (flux nominal)
```
uv run velos fetch                 # télécharge + empreinte (manifest à commiter)
uv run velos replay-init
uv run velos run                   # run de base (entraînement 2011)
uv run velos replay-next && uv run velos run --lot data/incoming/lot-2012-01.csv
uv run velos qualify --version 1 && uv run velos select --version 1 --reason "premier champion"
uv run velos rollback              # retour au champion précédent
uv run velos serve-flows           # mode Prefect planifié
```
## Incident contrôlé n°1 : lot corrompu
```
uv run velos make-bad-lot --kind negatif   # ou colonne | volume
uv run velos run --lot data/incoming/lot-2012-0X.csv   # code 2, quarantaine, rapport, alerte
```
## État de validation (honnêteté)
Testé (pytest, données synthétiques) : qualité, rejeu, pipeline, idempotence, quarantaine, qualification, sélection, rollback, CLI.
**Non exécuté dans l'environnement de développement** : MLflow réel, Prefect, Docker Compose, téléchargement UCI réel, `uv.lock`. À valider sur votre machine (voir `docs/TRANSFERT.md`).
