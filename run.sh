#!/usr/bin/env bash
#
# run.sh — one-shot setup and launch for the GeoDjango vacation-rentals project.
#
# It will, in order:
#   1. Check prerequisites (python3, docker, docker compose)
#   2. Create/activate the virtualenv and install Python requirements
#   3. Create .env from .env.example (first run only)
#   4. Start PostgreSQL (PostGIS + pgvector) via docker compose and wait for it
#   5. Apply Django migrations
#   6. Create the admin superuser from .env (first run only)
#   7. Seed the database from data/listings.csv (first run only, with embeddings)
#   8. Start the Django development server
#
# Usage:
#   ./run.sh                 # full setup + run server
#   ./run.sh --no-install    # skip pip install
#   ./run.sh --no-seed       # skip the data import
#   ./run.sh --no-server     # set everything up but don't start the server
#   ./run.sh -h | --help     # show this help
#
# The server binds to localhost by default (127.0.0.1). To expose it to other
# machines on the network, override the host explicitly:
#   SERVER_HOST=0.0.0.0 ./run.sh
#   SERVER_PORT=8080 ./run.sh
#
set -euo pipefail


SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

VENV_DIR="${VENV_DIR:-.venv}"
SERVER_HOST="${SERVER_HOST:-127.0.0.1}"
SERVER_PORT="${SERVER_PORT:-8000}"

SEED_FILE="${SEED_FILE:-data/listings.csv}"
SEED_COUNTRY="${SEED_COUNTRY:-United States}"
SEED_STATE="${SEED_STATE:-New York}"
SEED_CITY="${SEED_CITY:-Albany}"
if [[ -t 1 ]]; then
  C_RESET=$'\033[0m'; C_BLUE=$'\033[34m'; C_GREEN=$'\033[32m'
  C_YELLOW=$'\033[33m'; C_RED=$'\033[31m'; C_BOLD=$'\033[1m'
else
  C_RESET=""; C_BLUE=""; C_GREEN=""; C_YELLOW=""; C_RED=""; C_BOLD=""
fi
step() { printf '\n%s==> %s%s\n' "$C_BOLD$C_BLUE" "$*" "$C_RESET"; }
info() { printf '    %s\n' "$*"; }
ok()   { printf '    %s✓ %s%s\n' "$C_GREEN" "$*" "$C_RESET"; }
warn() { printf '    %s! %s%s\n' "$C_YELLOW" "$*" "$C_RESET"; }
die()  { printf '\n%serror: %s%s\n' "$C_RED" "$*" "$C_RESET" >&2; exit 1; }


DO_INSTALL=1
DO_SEED=1
DO_SERVER=1

for arg in "$@"; do
  case "$arg" in
    --no-install) DO_INSTALL=0 ;;
    --no-seed)    DO_SEED=0 ;;
    --no-server)  DO_SERVER=0 ;;
    -h|--help)
      awk 'NR==1{next} /^#/{sub(/^# ?/,""); print; next} {exit}' "${BASH_SOURCE[0]}"
      exit 0
      ;;
    *) die "Unknown option: $arg (try --help)" ;;
  esac
done


step "Checking prerequisites"

command -v python3 >/dev/null 2>&1 || die "python3 is not installed or not on PATH."
ok "python3 ($(python3 --version 2>&1))"

command -v docker >/dev/null 2>&1 || die "docker is not installed or not on PATH."
docker info >/dev/null 2>&1 || die "Docker daemon is not running. Start Docker and retry."

# Prefer the modern plugin, fall back to the legacy standalone binary.
if docker compose version >/dev/null 2>&1; then
  COMPOSE=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE=(docker-compose)
else
  die "Neither 'docker compose' nor 'docker-compose' is available."
fi
ok "Docker Compose (${COMPOSE[*]})"

step "Setting up Python environment"

if [[ ! -d "$VENV_DIR" ]]; then
  info "Creating virtualenv at $VENV_DIR ..."
  python3 -m venv "$VENV_DIR"
fi

source "$VENV_DIR/bin/activate"
ok "Using $(python --version 2>&1) from $VENV_DIR"

if [[ "$DO_INSTALL" -eq 1 ]]; then
  info "Installing Python requirements (this can take a while on first run)..."
  python -m pip install --upgrade pip >/dev/null
  python -m pip install -r requirements.txt
  ok "Requirements installed"
else
  warn "Skipping pip install (--no-install)"
fi
step "Configuring environment"

if [[ ! -f .env ]]; then
  if [[ -f .env.example ]]; then
    grep -v '^\[TEMPLATE\]' .env.example > .env
    ok "Created .env from .env.example"
  else
    die "No .env and no .env.example to copy from."
  fi
else
  ok ".env already present"
fi

while IFS='=' read -r key value; do
  key="${key//[[:space:]]/}"
  [[ -z "$key" ]] && continue
  export "$key=$value"
done < <(grep -E '^[A-Za-z_][A-Za-z0-9_]*=' .env || true)

DB_NAME="${DB_NAME:-geodjango}"
DB_USER="${DB_USER:-postgres}"


step "Starting PostgreSQL (PostGIS + pgvector)"

"${COMPOSE[@]}" up -d --build

info "Waiting for PostgreSQL to accept connections ..."
for _ in $(seq 1 60); do
  if "${COMPOSE[@]}" exec -T postgres \
       pg_isready -U "$DB_USER" -d "$DB_NAME" >/dev/null 2>&1; then
    ok "PostgreSQL is ready"
    break
  fi
  sleep 2
done
if ! "${COMPOSE[@]}" exec -T postgres \
       pg_isready -U "$DB_USER" -d "$DB_NAME" >/dev/null 2>&1; then
  die "PostgreSQL did not become ready in time. Check: ${COMPOSE[*]} logs postgres"
fi


step "Applying database migrations"
python manage.py migrate --noinput
ok "Migrations applied"


step "Creating admin user"

if [[ -z "${DJANGO_SUPERUSER_USERNAME:-}" || -z "${DJANGO_SUPERUSER_PASSWORD:-}" ]]; then
  warn "No DJANGO_SUPERUSER_USERNAME/PASSWORD in .env — skipping admin user"
else
  admin_exists="$(python manage.py shell -c "
from django.contrib.auth import get_user_model
import os
print('yes' if get_user_model().objects.filter(
    username=os.environ.get('DJANGO_SUPERUSER_USERNAME', '')
).exists() else 'no')
" | tail -n1)"

  if [[ "$admin_exists" == "yes" ]]; then
    ok "Admin user '$DJANGO_SUPERUSER_USERNAME' already exists"
  else
    python manage.py createsuperuser --noinput
    ok "Admin user '$DJANGO_SUPERUSER_USERNAME' created"
  fi
fi


step "Seeding data"

property_count="$(python manage.py shell -c \
  'from property.models import Property; print(Property.objects.count())' | tail -n1)"

if [[ "$DO_SEED" -eq 0 ]]; then
  warn "Skipping data import (--no-seed)"
elif [[ "${property_count:-0}" -gt 0 ]]; then
  ok "Database already has $property_count properties — skipping import"
elif [[ ! -f "$SEED_FILE" ]]; then
  warn "Seed file '$SEED_FILE' not found — skipping import"
else
  info "Importing '$SEED_FILE' as $SEED_CITY, $SEED_STATE, $SEED_COUNTRY ..."
  info "(first run downloads the all-MiniLM-L6-v2 model and builds embeddings)"
  python manage.py import_properties "$SEED_FILE" \
    --country "$SEED_COUNTRY" --state "$SEED_STATE" --city "$SEED_CITY"
  ok "Data imported"
fi


if [[ "$DO_SERVER" -eq 0 ]]; then
  step "Setup complete"
  info "Skipped starting the server (--no-server)."
  exit 0
fi

step "Starting Django development server"
info "Homepage :  http://127.0.0.1:${SERVER_PORT}/"
info "Admin    :  http://127.0.0.1:${SERVER_PORT}/admin/"
info "Press Ctrl+C to stop."
printf '\n'
exec python manage.py runserver "${SERVER_HOST}:${SERVER_PORT}"