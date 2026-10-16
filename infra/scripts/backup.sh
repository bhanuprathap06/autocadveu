#!/usr/bin/env bash
# Consistent backup of an AutoForge docker compose deployment: Postgres (custom-format dump) + object store archive.
#   infra/scripts/backup.sh ENV_FILE BACKUP_DIR
# Produces BACKUP_DIR/<timestamp>/{db.dump,objects.tar,SHA256SUMS}. Copy it off-host (encrypted) afterwards.
set -euo pipefail
ENV_FILE=${1:?env file}; OUT=${2:?backup dir}
HERE=$(cd "$(dirname "$0")/.." && pwd)
DC=(docker compose -f "$HERE/docker-compose.yml" --env-file "$ENV_FILE")
TS=$(date -u +%Y%m%dT%H%M%SZ)
DEST="$OUT/$TS"; mkdir -p "$DEST"
# Database first: object keys are content-addressed and immutable, so objects referenced by the dump are already
# stored before the dump starts; anything uploaded during the backup is simply not referenced yet.
"${DC[@]}" exec -T postgres pg_dump -U autoforge -d autoforge -Fc --no-owner > "$DEST/db.dump"
"${DC[@]}" run --rm --no-deps -T --entrypoint python api scripts/objects_backup.py export - > "$DEST/objects.tar"
(cd "$DEST" && sha256sum db.dump objects.tar > SHA256SUMS)
echo "backup written to $DEST"
