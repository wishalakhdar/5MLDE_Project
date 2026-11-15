# Architecture

```
UCI hour.csv --fetch--> data/raw (+manifest SHA-256)
   --replay-init--> data/store : base 2011 | lots 2012-01..11 | corpus 2012-12
lot (data/incoming) -> [1 ingestion+QUALITÉ bloquante] -> [2 prétraitement] -> [3 entraînement] -> [4 évaluation+publication atomique]
   -> release (registre MLflow, rechargement en nouveau processus, qualification) -> acceptation du lot (ledger)
   qualité KO => quarantaine data/rejected + rapport + alerte, rien d'autre ne s'exécute
registre : tags qualification ; alias champion posé par `velos select` (décision RECALCULÉE depuis evidence.json)
Prefect : process_lot_flow / watch_incoming_flow ; retries uniquement sur erreurs transitoires
Jeffrey : API (charge models:/…@champion) -> PostgreSQL tracing -> Grafana ; Locust
```
Choix clés : jamais de fuite (cnt/casual/registered hors features) ; découpage chronologique ; imputation ajustée sur le train ; corpus de qualification fixe pour comparer les versions ; une preuve absente n'est jamais une acceptation.
