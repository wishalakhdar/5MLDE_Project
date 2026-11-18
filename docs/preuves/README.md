# Preuves d'intégration (exécutions du 18/11/2026, période 19:15 → 20:15 sauf mention)

| Fichier | Ce qu'il prouve |
|---|---|
| `grafana-vue-ensemble.png` | Supervision : requêtes, latence p95, part d'erreurs 5xx et de refus 4xx, version servie, feedbacks sur la période |
| `grafana-erreurs-et-versions.png` | *Erreurs par code* : `invalid_input` (entrées refusées) et `model_unavailable` (incident n°2, MLflow arrêté) ; *Prédictions par version* : v3 servie après `select` + `/model/reload`, puis v1 après `rollback` |
| `grafana-chargements-modele.png` | Chargements du modèle tracés : v3 à 19:22 (mise en service), v1 à 19:33 (retour arrière), puis les rechargements de l'incident n°2 |
| `grafana-traitements.png` | Suivi des traitements : lot 2012-02 corrompu `rejected` (avec la cause), puis lot sain `succeeded` → version 3 |
| `mlflow-registre-v3-v2.png` | Registre : versions 3 et 2 avec leurs preuves en tags (run, origine des données, empreinte, rechargement, politique, verdict) |
| `mlflow-registre-v1-champion.png` | Registre : version 1 portant l'alias `champion` après le retour arrière |
| `prefect-flows.png` | Orchestration : 2 flows `velos-process-lot` réussis (6 tâches) et 2 en échec (lot corrompu bloqué à la 1re tâche) |
| `historique-selections.png` | Traçabilité des mises en service : `select` v1, `select` v3, `rollback` vers v1 (auteur, date, motif, politique) |

La panne PostgreSQL de l'incident n°2 n'apparaît pas dans Grafana (traces non enregistrables pendant la panne) :
elle est documentée par les logs de l'API dans `docs/DEMO.md` (section 6).
Résultats de charge : fichiers `loadtests/results/` (pas de capture nécessaire).
