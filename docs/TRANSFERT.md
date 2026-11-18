# Transfert Aicha → Jeffrey (jalon T)

## Checklist avant de passer la main
- [x] `uv sync` puis `uv.lock` commité ; `uv run pytest` vert ; CI verte
- [x] `data/manifest.json` commité (créé par `velos fetch`)
- [x] `docker compose --env-file .env -f infra/compose.yml up -d` : 4 services sains (noter les tags d'images réellement utilisés)
- [x] Run nominal sur les VRAIES données : fetch → replay-init → `velos run` (base) → `velos run --lot` ×N
- [x] Au moins 2 versions dans le registre, `velos qualify`, `velos select` (alias champion), `velos rollback` testé
- [x] Incident n°1 rejoué : `velos make-bad-lot` → code 2, rapport dans `data/rejected`, alerte reçue
- [x] Seuils de `policies/qualification-v1.json` calibres apres le premier run de base, avant tout verdict de qualification (ne jamais bouger un seuil pour faire passer un modèle ; sinon nouvelle version de politique + requalification)
- [x] Aucun secret dans Git (`git grep -i password` ne montre que `.env.example`)

## Session de 1 h avec Jeffrey
1. 10 min : architecture (docs/ARCHITECTURE.md) 2. 15 min : démo pipeline + incident 3. 15 min : contrat API 4. 10 min : schéma SQL + Grafana 5. 10 min : ce qui reste (API, persistance, Locust, Grafana, dimensionnement, intégration finale, synthèse OPS).

## Notes de validation (Aicha, Windows 11, Docker Desktop)
- Verifies : les 4 services Docker, le schema SQL `tracing`, les droits `api_writer` (lecture/ecriture, pas de DELETE) et `grafana_reader` (lecture seule), la connexion Grafana, le flow Prefect (lots nominaux), la surveillance `serve-flows`, un incident controle (lot corrompu : quarantaine, rapport, alerte, run de suivi en FAILED).
- Port PostgreSQL : le 5432 est reserve par Windows sur cette machine, donc POSTGRES_PORT=5433 dans .env (a l'interieur de Docker, il reste 5432).
- Alerte webhook : testee uniquement avec webhook.site (format {"text": ...}). Teams peut exiger un autre format.
- Non teste : coupure reseau reelle (les retries sont testes en simulation seulement).
- Seuils de qualification figes en version 1.1.0 : ne pas les deplacer pour faire passer un modele.

## Notes de validation (Jeffrey, Windows 11, Docker Desktop)
- Prérequis : virtualisation activée et WSL 2 installé (Docker Desktop ne démarrait pas sans).
- Fins de ligne : 00-roles.sh extrait en CRLF par Git sous Windows ; corrigé par .gitattributes (*.sh eol=lf).
- Tests : 55 passed (1 avertissement pandas dans synthetic.py : dayofweek déprécié).
- Docker : 4 services running, postgres healthy, POSTGRES_PORT=5432 ; rôles api_writer et grafana_reader et 4 tables tracing.* présents.
- Données : empreinte hour.csv identique (e03de4ee…), 17 379 lignes ; replay-init : base 8 645, corpus 742, 11 lots.
- Pipeline : velos run -> v1 production-ready, validation.mae=64.95, rechargement ok (delta 0.0).
- Alias champion posé sur v1 ; prédiction par alias OK (models:/velos-demand-cnt@champion, version 1).
- Grafana : source velos-postgres testée, « Database Connection OK ».