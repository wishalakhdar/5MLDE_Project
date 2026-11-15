#!/bin/bash
# Crée les rôles avec des mots de passe lus dans l'ENVIRONNEMENT (jamais écrits dans le dépôt).
set -euo pipefail
psql -v ON_ERROR_STOP=1 -U postgres -d velos <<SQL
CREATE ROLE api_writer LOGIN PASSWORD '${API_WRITER_PASSWORD}';
CREATE ROLE grafana_reader LOGIN PASSWORD '${GRAFANA_READER_PASSWORD}';
SQL
