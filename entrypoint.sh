#!/bin/bash
set -e

echo "Running database migrations..."
alembic upgrade head

echo "Seeding default fraud rules..."
python -m scripts.seed_rules

echo "Migrations complete. Starting application..."
exec "$@"
