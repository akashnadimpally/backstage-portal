#!/usr/bin/env bash

# Define configuration variables
ENV_FILE=".env"
KEY_NAME="POSTGRES_PASSWORD"
PASSWORD_LENGTH=32

# ── Free ports 3000 and 7007 before starting ──────────────────────────────────
echo "Freeing ports 3000 and 7007..."
lsof -ti:3000,7007 | xargs kill -9 2>/dev/null || true
sleep 1
# ─────────────────────────────────────────────────────────────────────────────

# Condition: Check if the .env file already exists
if [ -f "$ENV_FILE" ]; then
    echo "The ${ENV_FILE} file already exists. Skipping password generation."
else
    # Generate a cryptographically secure random password (LC_ALL=C fixes macOS tr error)
    NEW_PASSWORD=$(LC_ALL=C tr -dc 'A-Za-z0-9!@#%^*()-_=+' < /dev/urandom | head -c "$PASSWORD_LENGTH")

    # Create the file and write the key-value pair
    echo "${KEY_NAME}=\"${NEW_PASSWORD}\"" > "$ENV_FILE"
    echo "Created new ${ENV_FILE} file with ${KEY_NAME}."
fi

docker compose -f docker-compose-postgres.yml --env-file .env up -d

docker exec postgres_db env | grep POSTGRES

yarn install

# Load .env into the current shell so POSTGRES_PASSWORD is available
# to the Backstage backend process (yarn start does not auto-load .env)
set -a
# shellcheck source=.env
source "$ENV_FILE"
set +a

yarn start
