# Contrat d'API (pour Jeffrey)

Source de vérité : `contract/api_contract.json` + exemples `contract/example_*.json`.

- `POST /predict` : `{"instances":[{12 features}]}`. Validation = `velos.contract.validate_inference_frame` (réutilisez-la, ne la réécrivez pas). `cnt`, `casual`, `registered` sont INTERDITS en entrée.
- Prédiction : `velos.predict.predict_frame(model, df)` (clip ≥ 0).
- `POST /feedback` : `{prediction_id, observed_cnt}` → table `tracing.feedbacks`, **un seul feedback par prédiction** (second envoi : 409 `feedback_exists`).
- `GET /health` : `status` (`ok` / `degraded` / `unavailable`), modèle chargé (nom, version, alias, qualification) et état de la base.
- `POST /model/reload` : recharge le modèle désigné par l'alias sans redémarrer ; en cas d'échec, 503 et le modèle actuel est conservé.
- Modèle : `models:/velos-demand-cnt@champion`. Pas d'alias (ou MLflow injoignable) au démarrage → l'API démarre SANS modèle : `/health` et `/predict` répondent 503 jusqu'à un `POST /model/reload` réussi. Fonction à réutiliser : `velos.predict.load_alias_model(cfg)` (retourne modèle, uri, version) ; exemple en ligne de commande : `uv run velos predict --alias champion --input <csv> --output <csv>`. La version à renvoyer dans chaque réponse est celle retournée par cette fonction. Pour avancer sans attendre : `velos stub-model` (modèle SYNTHÉTIQUE, à ne jamais présenter comme réel).
- Erreurs : JSON `error_example.json`, codes 404 / 409 / 422 / 503 / 500, jamais de trace Python au client.
- Persistance : rôle `api_writer` (INSERT/SELECT) sur le schéma `tracing` (`infra/postgres/10-schema.sql`).
