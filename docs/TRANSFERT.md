# Transfert Aicha → Jeffrey (jalon T)

## Checklist avant de passer la main
- [ ] `uv sync` puis `uv.lock` commité ; `uv run pytest` vert ; CI verte
- [ ] `data/manifest.json` commité (créé par `velos fetch`)
- [ ] `docker compose --env-file .env -f infra/compose.yml up -d` : 4 services sains (noter les tags d'images réellement utilisés)
- [ ] Run nominal sur les VRAIES données : fetch → replay-init → `velos run` (base) → `velos run --lot` ×N
- [ ] Au moins 2 versions dans le registre, `velos qualify`, `velos select` (alias champion), `velos rollback` testé
- [ ] Incident n°1 rejoué : `velos make-bad-lot` → code 2, rapport dans `data/rejected`, alerte reçue
- [ ] Seuils de `policies/qualification-v1.json` calibrés AVANT de regarder les verdicts (ne jamais bouger un seuil pour faire passer un modèle ; sinon nouvelle version de politique + requalification)
- [ ] Aucun secret dans Git (`git grep -i password` ne montre que `.env.example`)

## Session de 1 h avec Jeffrey
1. 10 min : architecture (docs/ARCHITECTURE.md) 2. 15 min : démo pipeline + incident 3. 15 min : contrat API 4. 10 min : schéma SQL + Grafana 5. 10 min : ce qui reste (API, persistance, Locust, Grafana, dimensionnement, intégration finale, synthèse OPS).

## Notes de validation (Aicha, Windows 11, Docker Desktop)
- Verifies : les 4 services Docker, le schema SQL `tracing`, les droits `api_writer` (lecture/ecriture, pas de DELETE) et `grafana_reader` (lecture seule), la connexion Grafana, le flow Prefect (lots nominaux), la surveillance `serve-flows`, un incident controle (lot corrompu : quarantaine, rapport, alerte, run de suivi en FAILED).
- Port PostgreSQL : le 5432 est reserve par Windows sur cette machine, donc POSTGRES_PORT=5433 dans .env (a l'interieur de Docker, il reste 5432).
- Alerte webhook : testee uniquement avec webhook.site (format {"text": ...}). Teams peut exiger un autre format.
- Non teste : coupure reseau reelle (les retries sont testes en simulation seulement).
- Seuils de qualification figes en version 1.1.0 : ne pas les deplacer pour faire passer un modele.
