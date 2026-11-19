-- Un seul feedback par prédiction (contrainte feedbacks_prediction_id_unique).
-- Sur une base NEUVE, 10-schema.sql crée déjà la contrainte : ce script ne fait alors rien.
-- Sur une base EXISTANTE (créée avant cette correction), le lancer une fois à la main :
--   docker compose --env-file .env -f infra/compose.yml exec -T postgres psql -U postgres -d velos -f /docker-entrypoint-initdb.d/30-feedback-unique.sql
-- Idempotent : peut être relancé sans effet. Les doublons déjà présents sont supprimés en gardant
-- le PREMIER feedback reçu pour chaque prédiction (même règle que l'API : le second est refusé).
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'feedbacks_prediction_id_unique') THEN
    DELETE FROM tracing.feedbacks f
     USING tracing.feedbacks g
     WHERE f.prediction_id = g.prediction_id
       AND (f.created_at, f.feedback_id::text) > (g.created_at, g.feedback_id::text);
    ALTER TABLE tracing.feedbacks
      ADD CONSTRAINT feedbacks_prediction_id_unique UNIQUE (prediction_id);
    RAISE NOTICE 'Contrainte feedbacks_prediction_id_unique ajoutée.';
  ELSE
    RAISE NOTICE 'Contrainte feedbacks_prediction_id_unique déjà présente : rien à faire.';
  END IF;
END
$$;
