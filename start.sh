#!/usr/bin/env bash
# Start AutoForge on this machine with one command:   ./start.sh
# First run creates .env with random secrets and a local mail catcher; later runs reuse it.
#   ./start.sh stop     stop the stack (data is kept)
#   ./start.sh logs     follow the logs
set -euo pipefail
cd "$(dirname "$0")"

COMPOSE=(docker compose -f infra/docker-compose.yml --env-file .env --profile local-mail)

case "${1:-up}" in
  stop) exec "${COMPOSE[@]}" down ;;
  logs) exec "${COMPOSE[@]}" logs -f --tail=100 ;;
  up) ;;
  *) echo "usage: $0 [up|stop|logs]"; exit 2 ;;
esac

command -v docker >/dev/null || { echo "Docker is required: https://docs.docker.com/get-docker/"; exit 1; }

if [ ! -f .env ]; then
  if docker volume inspect autoforge_pgdata >/dev/null 2>&1; then
    echo "Existing AutoForge data was found, but .env (which holds its passwords) is missing."
    echo "Restore your .env, or delete the old data with:  docker volume rm autoforge_pgdata autoforge_objectdata autoforge_redisdata"
    exit 1
  fi
  echo "Creating .env with random secrets (local evaluation settings)"
  rand() { openssl rand -hex 24 2>/dev/null || head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n'; }
  sed -e "s/^POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD=$(rand)/" \
      -e "s/^S3_ACCESS_KEY=.*/S3_ACCESS_KEY=$(rand)/" \
      -e "s/^S3_SECRET_KEY=.*/S3_SECRET_KEY=$(rand)/" \
      -e "s/^SMTP_HOST=.*/SMTP_HOST=mail/" \
      -e "s/^SMTP_PORT=.*/SMTP_PORT=1025/" \
      -e "s/^SMTP_STARTTLS=.*/SMTP_STARTTLS=false/" \
      .env.example > .env
  chmod 600 .env
fi

"${COMPOSE[@]}" up -d --build

url=$(grep -E '^PUBLIC_BASE_URL=' .env | cut -d= -f2-)
printf "Waiting for the API"
for _ in $(seq 90); do
  if curl -fsSk "${url}/readyz" >/dev/null 2>&1; then
    echo
    echo "AutoForge is running:  ${url}"
    echo "Emails (verification links): http://127.0.0.1:${MAILPIT_PORT:-8025}"
    echo "The browser warns about the certificate on first visit (local CA for localhost); accept it to continue."
    exit 0
  fi
  printf "."; sleep 2
done
echo; echo "Not ready after 3 minutes. Check: ./start.sh logs"; exit 1
