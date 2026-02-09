#!/bin/bash
set -e

# Only run migrations and seeding for the HTTP service (default CMD).
# Celery workers and Kafka consumers skip this since the HTTP service
# must be healthy before they start (depends_on condition).
if echo "$@" | grep -q "uvicorn"; then
    echo "Running database migrations..."
    alembic upgrade head || echo "WARNING: Migrations failed — check database connectivity"

    echo "Seeding fraud rules..."
    python -m scripts.seed_rules || echo "WARNING: Seeding failed — rules may already exist"
fi

echo "Starting: $@"
exec "$@"
