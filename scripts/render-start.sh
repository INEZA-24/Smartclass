#!/bin/sh
set -eu

: "${SECRET_KEY:?SECRET_KEY is required}"
: "${DATABASE_URL:?DATABASE_URL is required}"

flask --app wsgi:app db upgrade
flask --app wsgi:app seed
flask --app wsgi:app provision-admin-from-env

unset INITIAL_ADMIN_USERNAME
unset INITIAL_ADMIN_FULL_NAME
unset INITIAL_ADMIN_PASSWORD

exec gunicorn wsgi:app \
  --bind "0.0.0.0:${PORT:-10000}" \
  --workers "${GUNICORN_WORKERS:-1}" \
  --threads "${GUNICORN_THREADS:-2}" \
  --timeout "${GUNICORN_TIMEOUT:-60}" \
  --access-logfile - \
  --error-logfile -
