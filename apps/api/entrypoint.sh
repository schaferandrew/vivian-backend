#!/bin/bash
set -e

echo "Waiting for PostgreSQL to be ready..."

# Extract host from DATABASE_URL
DB_HOST=$(echo "$DATABASE_URL" | sed -E 's|.*@([^:]+):.*|\1|')

# Wait for PostgreSQL
until pg_isready -h "$DB_HOST" -p 5432 -U postgres; do
    echo "PostgreSQL is unavailable - sleeping"
    sleep 1
done

echo "PostgreSQL is ready!"

echo "Running database migrations..."
cd /app
alembic -c alembic.ini upgrade head

echo "Starting API server..."
exec python -m vivian_api.main
