#!/usr/bin/env bash
# Restore a backup produced by backup.sh into a (new or existing) deployment, then verify integrity.
#   infra/scripts/restore.sh ENV_FILE BACKUP_DIR/<timestamp>
# WARNING: replaces the current database contents.
set -euo pipefail
ENV_FILE=${1:?env file}; SRC=${2:?backup directory}
HERE=$(cd "$(dirname "$0")/.." && pwd)
DC=(docker compose -f "$HERE/docker-compose.yml" --env-file "$ENV_FILE")
(cd "$SRC" && sha256sum -c SHA256SUMS)
"${DC[@]}" up -d postgres redis objects
"${DC[@]}" stop api worker web 2>/dev/null || true
until "${DC[@]}" exec -T postgres pg_isready -U autoforge -d autoforge >/dev/null; do sleep 1; done
"${DC[@]}" exec -T postgres pg_restore -U autoforge -d autoforge --clean --if-exists --no-owner < "$SRC/db.dump"
"${DC[@]}" run --rm --no-deps --entrypoint python api -m autoforge.cli ensure-bucket
"${DC[@]}" run --rm --no-deps -T --entrypoint python api scripts/objects_backup.py import - < "$SRC/objects.tar"
"${DC[@]}" run --rm --no-deps --entrypoint python api -m autoforge.cli migrate
"${DC[@]}" run --rm --no-deps --entrypoint python api scripts/verify_integrity.py
"${DC[@]}" up -d
echo "restore complete"
