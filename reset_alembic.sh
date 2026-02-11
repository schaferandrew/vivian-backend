#!/bin/bash
# Reset alembic to work with the new single migration

echo "Resetting alembic state..."

# Get into the API container and fix the alembic version
docker compose exec -T api psql -h postgres -U postgres -d vivian << 'EOF'
-- Drop alembic version tracking
DROP TABLE IF EXISTS alembic_version;
EOF

echo "Alembic state reset. Now running migrations..."
docker compose exec api alembic upgrade head

echo "Done!"
