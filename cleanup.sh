#!/bin/bash

# Kill any processes still holding the Backstage ports
echo "Freeing ports 3000 and 7007..."
lsof -ti:3000,7007 | xargs kill -9 2>/dev/null || true

docker compose -f docker-compose-postgres.yml --env-file .env down
docker rm -f postgres_db