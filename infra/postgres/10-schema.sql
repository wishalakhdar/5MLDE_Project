-- Schéma de traçabilité. api_writer : SELECT/INSERT seulement. grafana_reader : SELECT seulement.
CREATE SCHEMA IF NOT EXISTS tracing;

CREATE TABLE tracing.model_versions (
    model_name     text        NOT NULL,
    version        integer     NOT NULL,
    alias          text,
    qualification  text        NOT NULL,
    data_origin    text        NOT NULL,
    loaded_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (model_name, version, loaded_at)
);

CREATE TABLE tracing.api_requests (
    request_id     uuid        PRIMARY KEY,
    received_at    timestamptz NOT NULL DEFAULT now(),
    endpoint       text        NOT NULL,
    status_code    integer     NOT NULL,
    latency_ms     double precision NOT NULL,
    model_version  integer,
    error_code     text
);

CREATE TABLE tracing.predictions (
    prediction_id  uuid        PRIMARY KEY,
    request_id     uuid        NOT NULL REFERENCES tracing.api_requests(request_id),
    created_at     timestamptz NOT NULL DEFAULT now(),
    model_version  integer     NOT NULL,
    features       jsonb       NOT NULL,
    predicted_cnt  double precision NOT NULL CHECK (predicted_cnt >= 0)
);

CREATE TABLE tracing.feedbacks (
    feedback_id    uuid        PRIMARY KEY,
    prediction_id  uuid        NOT NULL REFERENCES tracing.predictions(prediction_id),
    created_at     timestamptz NOT NULL DEFAULT now(),
    observed_cnt   double precision NOT NULL CHECK (observed_cnt >= 0),
    -- Un seul feedback par prédiction : un second envoi est refusé (409 feedback_exists).
    CONSTRAINT feedbacks_prediction_id_unique UNIQUE (prediction_id)
);

CREATE INDEX ON tracing.api_requests (received_at);
CREATE INDEX ON tracing.predictions (created_at);

GRANT USAGE ON SCHEMA tracing TO api_writer, grafana_reader;
GRANT SELECT, INSERT ON ALL TABLES IN SCHEMA tracing TO api_writer;
GRANT SELECT ON ALL TABLES IN SCHEMA tracing TO grafana_reader;
