# Synthèse des choix et résultats OPS — projet Vélos (5MLDE)

Binôme : Aicha (phase 1 : données, pipeline, MLflow, Prefect) et Jeffrey (phase 2 : API, persistance, charge,
supervision, intégration). Mesures et démonstrations réalisées le 18/11/2026 sur Windows 11 + Docker Desktop
(WSL 2, 8 CPU), avec les vraies données UCI. Commandes de reproduction : `README.md` et `docs/DEMO.md`.

## 1. Sujet et données
- **Objectif** : prévoir le nombre de locations de vélos par heure (`cnt`, régression) à partir du calendrier et de la
  météo. Modèle volontairement simple : `RandomForestRegressor` comparé à une baseline (moyenne du train).
- **Données** : UCI Bike Sharing, `hour.csv` (17 379 lignes), licence CC BY 4.0 (`data/PROVENANCE.md`). Empreinte
  SHA-256 vérifiée à chaque récupération (`data/manifest.json`), identique sur les deux machines du binôme.
- **Rejeu par lots** (jeu historique) : base = 2011, lots mensuels 2012-01 à 2012-11, corpus de qualification fixe =
  2012-12, jamais utilisé pour entraîner.

## 2. Architecture
```
UCI ─fetch─► data/raw (SHA-256) ─replay─► lots ─► Prefect : ingestion + QUALITÉ bloquante → prétraitement
   → entraînement → évaluation → registre MLflow (qualification, tags) → acceptation du lot ; journal pipeline_runs
                                              │ alias champion (velos select / rollback)
Locust ──► API FastAPI (Docker, modèle chargé par alias) ──► PostgreSQL « tracing » ──► Grafana (dashboard versionné)
```
Cinq services Docker Compose : PostgreSQL, MLflow (SQLite + artefacts servis), Prefect, Grafana, API.

## 3. Décisions techniques et justifications
| Décision | Justification |
|---|---|
| Code en package `src/velos`, 4 étapes qui s'échangent des dataclasses, configuration JSON, `uv.lock` | Chapitre 2 : modularité, reproductibilité, pas de notebook ni de variable globale |
| Contrôles qualité codés en Python (`quality.py`) plutôt que Great Expectations | Règles simples et testables unitairement, sans dépendance lourde ; même rôle qu'une suite GX : bloquer avant le prétraitement, rapport conservé, quarantaine |
| Découpage chronologique, corpus de qualification fixe | Éviter la fuite temporelle ; comparer toutes les versions sur le même examen |
| Politique de qualification versionnée (1.1.0) et sélection recalculée depuis les preuves du run | Une mise en service ne repose jamais sur un tag modifiable à la main ; gain ≥ 1 % exigé pour remplacer le champion |
| Modèles sauvegardés au format cloudpickle (MLflow recommande `skops`) | Pipeline scikit-learn standard, rechargé et vérifié dans un nouveau processus (écart 0,0) ; à migrer vers `skops` (limite) |
| **API chargée par alias `champion`**, une fois au démarrage ; `POST /model/reload` | Chapitre 6 : pas de version codée en dur ni de redéploiement à chaque changement de champion ; pas de rechargement à chaque requête |
| Validation en deux temps : Pydantic (forme du JSON) puis contrat du modèle (`validate_inference_frame`) | Les règles métier ne sont écrites qu'une fois, partagées avec le pipeline |
| Erreurs JSON normalisées (422 / 503 / 500) avec `request_id`, sans trace Python | Exploitabilité côté client, sécurité |
| Traces dans PostgreSQL (schéma `tracing`), une transaction par requête | Persistance des prédictions, versions, durées, erreurs et feedbacks ; une seule source pour Grafana |
| Rôles séparés : `api_writer`, `pipeline_writer` (INSERT/SELECT), `grafana_reader` (SELECT) ; secrets dans `.env` | Moindre privilège, aucun secret versionné |
| Panne de base : prédictions maintenues, `/health` = `degraded`, feedbacks en 503 | La disponibilité des prédictions prime ; un feedback n'a de sens qu'enregistré |
| Grafana lit directement PostgreSQL (pas de Prometheus) | Schéma fourni par l'enseignant ; les métriques d'usage sont déjà dans les traces ; un composant de moins |
| API conteneurisée, ressources réglables (`API_CPUS`, `API_WORKERS`, `API_MEMORY`) | Comparer des configurations reproductibles pendant les tests de charge |
| Connexion PostgreSQL en `127.0.0.1` (et non `localhost`) | **Mesuré** : sous Windows, `localhost` tentait d'abord IPv6 ; chaque connexion coûtait 3 046 ms |
| Reprises du client MLflow bornées dans l'API (`MLFLOW_HTTP_REQUEST_MAX_RETRIES=2`) | **Constaté pendant l'incident n°2** : sans borne, l'API restait injoignable plusieurs minutes au démarrage |
| Healthcheck MLflow ; l'API attend que MLflow soit prêt | Au premier démarrage, l'API interrogeait MLflow avant qu'il réponde |
| Journal des traitements « au mieux » depuis le flow Prefect | Grafana suit les traitements ; une panne du suivi ne fait jamais échouer un entraînement |

## 4. Preuves d'intégration (exécutions réelles)
| Scénario | Résultat observé |
|---|---|
| Reproduction sur une 2e machine depuis le README | 55 tests verts, mêmes empreintes de données ; correction des fins de ligne (`.gitattributes`) découverte à cette occasion |
| Pipeline nominal (base) | v1 `production-ready`, MAE de validation 64,95, rechargement identique |
| Lots via Prefect | lot 2012-01 → v2 (MAE 68,57) ; lot 2012-02 → v3 (MAE 61,07) |
| Incident n°1 : lot corrompu | flow `Failed`, lot en quarantaine avec rapport, run MLflow `FAILED`, alerte, aucune version créée ; traitement `rejected` visible dans Grafana |
| Règle de mise en service | `select v3` accepté (+6 % de MAE sur v1) ; `select v2` **refusé** (gain −12,3 % < 1 %) |
| Changement de champion sans redéploiement | `/health` reste en v1 après `select` ; `/model/reload` → v3 (`previous_version` 1) ; prédictions en v3 |
| Retour arrière | `rollback` → v1, `/model/reload` → v1 ; historique `select, select, rollback` avec auteur, date et motif |
| API | prédiction 542,04 locations (mercredi de juillet, 17 h) avec la version ; `hr = 25` → 422 avec la cause |
| Persistance | requêtes (200 / 422 avec durée), prédictions (features en JSON), feedbacks, chargements de modèle en base |
| Incident n°2 : PostgreSQL arrêté | `/health` `degraded`, `/predict` 200, `/feedback` 503, « Trace perdue » journalisée ; reprise automatique (201). Invisible dans Grafana (traces de la période perdues) : visible dans les logs |
| Incident n°2 : MLflow arrêté | reload 503 avec modèle conservé ; redémarrage sans modèle en < 30 s (503) ; reload → v1 au retour du registre ; pics `model_unavailable` dans Grafana |
| Supervision | dashboard : ≈ 24 000 requêtes, 0,04 % de 5xx, 8,65 % de 4xx (≈ 1/11 : tâche « entrée invalide » de Locust), versions servies, traitements |

Captures commentées : `docs/preuves/README.md`. Historique des sélections : `registry/selection-history.jsonl` (local).

## 5. Tests de charge
**Protocole** (`docs/CHARGE.md`, fixé avant les mesures) : scénario Locust réaliste (prédiction d'une heure, d'une
journée, feedback, entrée invalide) ; paliers de 10, 20, 40, 80 utilisateurs (45 s, après échauffement) ; CPU et
mémoire relevés par `docker stats` ; objectif : **p95 ≤ 500 ms et < 1 % d'échecs à 40 utilisateurs** ; règle : la
configuration la moins coûteuse qui respecte l'objectif.

| Configuration | Débit max | Palier max dans l'objectif | p95 à ce palier | Mémoire max |
|---|---:|---:|---:|---:|
| A — 0,5 CPU, 1 processus | ~11 req/s | aucun (p95 580 ms dès 10 utilisateurs) | — | 243 Mio |
| B — 1 CPU, 1 processus | ~21 req/s | 10 | 150 ms | 254 Mio |
| C — 1 CPU, 2 processus | ~18 req/s | 10 | 110 ms | 470 Mio |
| D — 2 CPU, 2 processus | ~35 req/s | 20 | 170 ms | 472 Mio |
| E — 4 CPU, 4 processus | ~57 req/s | **40** | **290 ms** | 875 Mio |

Constats :
- **Service limité par le CPU** : environ 15 à 18 req/s par CPU (≈ 40 ms de CPU par requête : validation, forêt
  aléatoire, écriture en base) ; au-delà, le débit plafonne et seule l'attente augmente.
- **Mémoire ≈ 220 Mio par processus**, indépendante de la charge (chaque processus charge son modèle).
- **Plus de processus que de CPU n'apporte rien** (C : même débit que B, mémoire × 1,8, 1,5 % d'échecs à 80).
- **Aucune erreur jusqu'à 40 utilisateurs** quelle que soit la configuration ; le service ralentit sans casser.
- Sous saturation extrême, des feedbacks ont attendu une connexion plus de 3 s (`storage_unavailable`) : le pool de
  connexions devient un point de contention au-delà de la capacité mesurée.

## 6. Dimensionnement recommandé
- **Objectif de 40 utilisateurs simultanés : 4 CPU, 4 processus Uvicorn, 2 Go** — p95 290 ms (marge de 42 % sur
  500 ms), 0 échec, CPU moyen 124 % sur 400 %, mémoire 875 Mio.
- **Jusqu'à 20 utilisateurs : 2 CPU, 2 processus, 1 Go** — p95 170 ms.
- **Au-delà de 40** : réplication horizontale de conteneurs E derrière un répartiteur, avec un plafond de réplicas
  pour borner les coûts (chapitre 6). E dépasse l'objectif à 80 utilisateurs (p95 1,2 s).
- Ne jamais configurer plus de processus que de CPU ; prévoir ~250 Mio de mémoire par processus.

## 7. Qualité et automatisation
105 tests automatisés (`uv run pytest`) et un test d'intégration PostgreSQL à la demande. CI GitHub : tests, parcours
de bout en bout sur données synthétiques avec lot corrompu (code 2 attendu), validation de la configuration Compose
et construction de l'image de l'API depuis `uv.lock`.

## 8. Limites connues
- Mesures de charge sur une seule machine : Locust, l'API et PostgreSQL partagent le CPU ; une série par
  configuration (écart observé ~15 % entre deux séries de B) ; E ajoutée après la première série (objectif inchangé) ;
  mémoire de E probablement réductible (~1,25 Go), non mesurée.
- Une panne de PostgreSQL fait perdre les traces de la période (pas de file d'attente locale rejouée ensuite) :
  elle n'apparaît donc pas dans le dashboard, seulement dans les logs de l'API. Une alerte sur `/health` = `degraded`
  (sonde externe) serait nécessaire en production.
- Le tag de version `qualification.report` contient un chemin local de la machine qui a qualifié la version
  (peu portable ; le rapport lui-même est aussi journalisé dans le run MLflow).
- `POST /model/reload` ne recharge que le processus qui reçoit la requête : avec plusieurs processus, redémarrer le
  conteneur ; endpoint non authentifié (usage local).
- Modèles en cloudpickle (MLflow recommande `skops`) ; avertissement `httpx` déprécié pour le client de test.
- Feedbacks des tests de charge aléatoires : l'écart prédiction / observation du dashboard n'a de sens qu'avec des
  feedbacks réels.
- Alerte vérifiée uniquement avec un récepteur de test ; reprises réseau du pipeline testées en simulation.
- Seuils de qualification calibrés une fois (politique 1.1.0) ; toute évolution exige une nouvelle version de
  politique et une requalification.
- Démonstration locale : pas de TLS, pas de sauvegarde des volumes Docker.
