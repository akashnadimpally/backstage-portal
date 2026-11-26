# Docker & Docker Compose Usage Guide

## Overview

This project includes Docker Compose configurations for running Backstage with kDashM Kubernetes integration across different platforms. Choose the appropriate compose file for your platform.

## Files Structure

### Docker Compose Files
- **`docker-compose.linux.yml`** — Complete stack for Linux containers (backend + frontend + postgres)
- **`docker-compose.windows.yml`** — Complete stack for Windows containers (backend + frontend + postgres)

### Dockerfiles
- **`Dockerfile.backend.linux`** — Backend service for Linux
- **`Dockerfile.backend.windows`** — Backend service for Windows  
- **`Dockerfile.frontend.linux`** — Frontend service for Linux (nginx + React SPA)
- **`Dockerfile.frontend.windows`** — Frontend service for Windows (Node.js static server)

## Prerequisites

Before running Docker Compose, build the application artifacts in the repo root:

```bash
# Install dependencies
yarn install --immutable

# Compile TypeScript
yarn tsc

# Build backend
yarn build:backend

# Build frontend
yarn workspace app build
```

## Quick Start

### Linux Containers

```bash
# Start the complete stack
docker compose -f docker-compose.linux.yml up --build

# Run in background
docker compose -f docker-compose.linux.yml up -d --build

# View logs
docker compose -f docker-compose.linux.yml logs -f

# Stop the stack
docker compose -f docker-compose.linux.yml down

# Clean up including database volumes
docker compose -f docker-compose.linux.yml down -v
```

### Windows Containers

**Requirements:** Docker Desktop with Windows container mode enabled.

```bash
# Start the complete stack
docker compose -f docker-compose.windows.yml up --build

# Run in background
docker compose -f docker-compose.windows.yml up -d --build

# View logs
docker compose -f docker-compose.windows.yml logs -f

# Stop the stack
docker compose -f docker-compose.windows.yml down

# Clean up including database volumes
docker compose -f docker-compose.windows.yml down -v
```

## Access Points

Once the stack is running:

- **Frontend (Web UI):** http://localhost:3000
- **Backend API:** http://localhost:7007
- **Backend Health:** http://localhost:7007/healthcheck
- **Database (PostgreSQL):** localhost:5432
  - Username: `backstage`
  - Password: `backstage` (or set via `POSTGRES_PASSWORD` env var)

## Environment Variables

Create a `.env` file in the repo root to override defaults:

```env
# GitHub Integration (optional)
GITHUB_TOKEN=ghp_your_token_here
GITHUB_ORG=your-org

# GitLab Integration (optional; hostname only, without https://)
GITLAB_HOST=gitlab.example.com
GITLAB_TOKEN=your_gitlab_access_token

# Database Configuration
POSTGRES_HOST=postgres          # Default
POSTGRES_PORT=5432             # Default
POSTGRES_USER=backstage        # Default
POSTGRES_PASSWORD=backstage    # Default
POSTGRES_DB=backstage          # Default

# Public frontend URL. For remote VM access, replace localhost with the VM IP or DNS name.
APP_BASE_URL=http://localhost:3000
```

## Services

### PostgreSQL (postgres)
- **Image:** `postgres:17-alpine`
- **Port:** 5432
- **Volume:** `backstage_data` (Linux) or `backstage_data_win` (Windows)
- **Health Check:** Every 10s

### Backstage Backend (backstage-backend)
- **Image:** `backstage-backend:linux` or `backstage-backend:windows`
- **Port:** 7007 (internal Compose network only; Linux Compose does not publish it on the host)
- **Volumes:** 
  - `app-config.yaml` (read-only)
  - `app-config.production.yaml` (read-only)
  - `examples/` (read-only)
  - `templates/` (read-only)
- **Health Check:** Every 30s
- **Depends On:** PostgreSQL (service_healthy)

### Backstage Frontend (backstage-frontend)
- **Image:** `backstage-frontend:linux` or `backstage-frontend:windows`
- **Port:** 3000
- **API routing:** Nginx forwards `/api/*` to the backend over the internal Compose network.
- **Depends On:** Backend (service_healthy)
- **Health Check:** Every 30s

## Advanced Usage

### Run without PostgreSQL

If using an external database, you can skip PostgreSQL:

```bash
# Linux
docker compose -f docker-compose.linux.yml up -d postgres 
docker compose -f docker-compose.linux.yml up backstage-backend backstage-frontend

# Windows
docker compose -f docker-compose.windows.yml up -d postgres
docker compose -f docker-compose.windows.yml up backstage-backend backstage-frontend
```

Or set `POSTGRES_HOST` to point to your external database.

### View Service Logs

```bash
# All services
docker compose -f docker-compose.linux.yml logs -f

# Specific service
docker compose -f docker-compose.linux.yml logs -f backstage-backend
docker compose -f docker-compose.linux.yml logs -f backstage-frontend
docker compose -f docker-compose.linux.yml logs -f postgres
```

### Execute Commands in Running Containers

```bash
# Connect to backend container
docker compose -f docker-compose.linux.yml exec backstage-backend sh

# Connect to database
docker compose -f docker-compose.linux.yml exec postgres psql -U backstage -d backstage

# View backend process
docker compose -f docker-compose.linux.yml exec backstage-backend ps aux
```

### Rebuild Images

```bash
# Force rebuild (ignores cache)
docker compose -f docker-compose.linux.yml build --no-cache

# Rebuild and start
docker compose -f docker-compose.linux.yml up --build --force-recreate
```

### Inspect Services

```bash
# List containers
docker compose -f docker-compose.linux.yml ps

# View resource usage
docker compose -f docker-compose.linux.yml stats
```

## Troubleshooting

### Port Already in Use

If port 3000 or 7007 is already in use:

1. Find the process: `lsof -i :3000` (Linux/Mac) or `netstat -ano | findstr :3000` (Windows)
2. Kill the process or modify the port in the compose file
3. Restart the stack

### Database Connection Issues

If the backend can't connect to PostgreSQL:

1. Check the postgres service is running: `docker compose logs postgres`
2. Verify `POSTGRES_HOST` environment variable (should be `postgres` by default)
3. Ensure `POSTGRES_PASSWORD` matches between frontend and backend env vars

### Frontend Upstream Errors

If the frontend shows "upstream connect error":

1. Ensure backend service is healthy: `docker compose logs backstage-backend`
2. Check backend health endpoint: `curl http://localhost:7007/healthcheck`
3. Verify CORS configuration in `app-config.yaml`

### Windows Container Issues

- Docker Desktop may need Windows container mode enabled (right-click Docker icon → Switch to Windows containers)
- Windows containers are larger (~5GB) and slower to build initially
- PostgreSQL on Windows containers runs through Linux subsystem - for production, consider external database

## Kubernetes Integration (kDashM)

The kDashM dashboard is now integrated into Backstage. Once running:

1. Navigate to http://localhost:3000/kdashm
2. The dashboard connects to your local Kubernetes cluster via kubectl proxy (127.0.0.1:8001)
3. Supports Docker Desktop Kubernetes, minikube, Kind, and AKS clusters

For AKS support, ensure Azure CLI is signed in:
```bash
az login
az account set --subscription <subscription-id>
```

## Production Deployment

For production use:

1. Use managed PostgreSQL (Azure Database for PostgreSQL, AWS RDS, etc.)
2. Set `NODE_ENV=production` 
3. Configure proper authentication (GitHub OAuth, OIDC, etc.)
4. Use private container registries
5. Implement health checks and restart policies
6. Use resource limits and reservations
7. Enable logging and monitoring

See `app-config.production.yaml` for production configuration examples.
