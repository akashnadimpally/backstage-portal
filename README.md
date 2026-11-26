# Backstage Skynet

Backstage Skynet is an internal developer portal built on [Backstage](https://backstage.io). It brings a software catalog, repository scaffolding, documentation, Kubernetes operations, and Azure resource visibility into one web application.

## What It Includes

- **Software catalog:** browse components, APIs, systems, ownership, and relationships; import catalog entities; explore dependency graphs; and view API documentation. Checked-in entities are examples, not production inventory.
- **Software templates:** scaffold standard repositories on GitHub or GitLab, or create a Python application repository on GitHub. Templates register the generated component in the catalog.
- **GitHub organization dashboard (`/github-org`):** select a GitHub account or organization and review repositories, open pull requests, and GitHub Actions workflow runs.
- **GitHub and GitLab integrations:** use GitHub for organization/repository data and publishing. GitLab supports repository integration and scaffolding, including self-hosted instances; GitLab sign-in and a GitLab organization dashboard are not currently configured.
- **Kubernetes dashboard (`/kdashm`):** switch kubeconfig contexts, paste an additional kubeconfig, discover and connect to AKS clusters, and inspect namespaces, workloads, nodes, networking, storage, RBAC, autoscaling, events, and custom resources.
- **Kubernetes operations and observability:** view pod logs and metrics, explore raw metrics and analytics, map resource relationships, edit/apply resource YAML, restart deployments, delete supported resources, and browse/install Helm charts and releases.
- **Kubernetes assistant:** an optional model-backed assistant can analyze cluster context and warning events, with quick checks for pod errors, restart loops, resource use, security findings, events, and cluster health. Configure models in `kdashm-agent-models.json`.
- **Azure cloud page (`/cloud`):** sign in with Microsoft device code, browse subscriptions and resource groups, inspect subscription resources, and review month-to-date cost, forecast month-end cost, and available billing-period dates. Billing-period dates depend on the subscription being supported by Azure's Billing Periods API.
- **Infrastructure Agent (`/infra-agent`):** transform plain-text cloud requirements into production-ready Terraform deployments using approved modules, deterministic policy checks, automated review/repair loops, and human-in-the-loop HMAC approval tokens.
- **Backstage services:** catalog and TechDocs search backed by PostgreSQL, TechDocs, notifications/signals, user settings, API docs, catalog graph/import, GitHub pull-request board, Kubernetes plugin, and MCP Actions backend.

The GitHub organization, Azure, and Kubernetes views need the corresponding credentials and runtime access described below. Catalog contents and the local example template are sample data intended to be replaced or extended.

### Software Templates

The Scaffolder includes the following project archetypes. The new archetypes publish to the configured GitLab instance; the existing GitHub Repository template continues to publish to GitHub.

- REST API Service, Event-Driven Service, Background Worker
- React Frontend, Full Stack Application
- Kubernetes Workload, Helm Chart, AKS Application
- Terraform Module, Terraform Environment
- GitHub Repository
- AI Agent, Single Agent, Multi-Agent System, RAG Application, MCP Server
- ML Training Pipeline (continuous tabular supervised learning), NLP Training Pipeline (continuous text classification)

The ML and NLP pipelines expose `POST /records` for continuously pushed records, persist incoming data in local SQLite, create a stable holdout split, report evaluation metrics from `GET /model/status`, and retrain after their configured threshold of new labeled rows. The templates ask users to configure feature/target fields, text/label fields, starter model choice, initial training size, and retrain interval. Custom models plug in through a Python model factory implementing scikit-learn's `fit`/`predict` interface. Supervised training requires labels; unlabeled records do not count toward training.

These streaming starters are single-instance development scaffolds: SQLite and model artifacts are local, not a distributed queue or model registry. Connect a durable broker/source adapter and shared storage, then add authentication, monitoring, and model approval before production use. AI and RAG projects use an OpenAI-compatible API client and require model-specific `MODEL_API_KEY`, `MODEL_NAME`, and (where applicable) `MODEL_BASE_URL` and `EMBEDDING_MODEL` environment variables. They do not provision model services. The Terraform templates are provider-neutral starter scaffolds, and the AKS template generates manifests but does not provision a cluster or configure workload identity federation.

## Architecture

The application is a Yarn workspaces monorepo. `packages/app` contains the Backstage frontend and custom pages; `packages/backend` hosts the Backstage backend plugins and custom Kubernetes/Azure routes. PostgreSQL is used for the backend and search database. The Linux and Windows Docker Compose configurations run the backend, static frontend, and PostgreSQL together.

```text
Browser :3000
   |-- Backstage frontend (packages/app)
   |-- Backstage backend API :7007 (packages/backend)
	   |-- PostgreSQL :5432
	   |-- GitHub / GitLab integrations
	   |-- kubectl / Helm / Azure CLI for cluster and cloud features
```

## Repository Layout

| Path                         | Purpose                                                                       |
| ---------------------------- | ----------------------------------------------------------------------------- |
| `packages/app/`              | Backstage frontend, navigation, auth UI, Kubernetes dashboard, and Azure page |
| `packages/backend/`          | Backstage backend plugins, Kubernetes APIs, AKS discovery, and Azure routes   |
| `infra-agent/`               | Azure Terraform infrastructure agent with module catalog and LLM pipeline      |
| `templates/`                 | GitHub repository, GitLab repository, and Python application templates        |
| `examples/`                  | Sample catalog entities, organizational data, and a local template            |
| `app-config.yaml`            | Development configuration and integration settings                            |
| `app-config.production.yaml` | Production backend/database and catalog configuration                         |
| `docker-compose.*.yml`       | Full Linux or Windows container stack                                         |
| `Dockerfile.*`               | Backend and frontend images for Linux or Windows                              |
| `DOCKER_USAGE.md`            | Detailed container build, run, and troubleshooting guide                      |

## Requirements

- Node.js 22 or 24 and Yarn 4.13.0 (the version is pinned in `package.json`); Corepack can provide the pinned Yarn version.
- PostgreSQL for local development, or use the PostgreSQL service in Docker Compose.
- Docker Desktop or Docker Engine with Compose for containerized use.
- For Kubernetes operations: `kubectl`, Helm, a usable kubeconfig, and Kubernetes RBAC permissions for the actions you intend to perform.
- For Azure views and AKS discovery: Azure CLI (`az`) installed and authenticated with access to the relevant subscriptions.

## Local Development

Run these commands from the repository root:

```powershell
Copy-Item .env.example .env
yarn install --immutable
node --env-file=.env ./node_modules/@backstage/cli/bin/backstage-cli repo start
```

The Node command loads `.env` into the process; `yarn start` by itself does not. Before starting, configure a reachable PostgreSQL database and set `POSTGRES_PASSWORD` in `.env` to match it. The development config connects to PostgreSQL at `localhost:5432`. Sign-in uses the local email/password provider and requires a catalog-linked account.

Backstage Community RBAC replaces the allow-all permission policy. Administrators manage roles and assign fine-grained permissions at `/rbac`; KDashM separately enforces permissions for viewing cluster data, changing contexts, creating/updating/deleting resources, installing Helm charts, and using the AI agent.

### User Onboarding and Roles

On first startup, set `LOCAL_AUTH_ADMIN_PASSWORD` in `.env` to a unique secret of at least 12 characters. The backend creates the initial `admin@portal.dev.skynet` account bound to `user:default/admin`; it stores a salted scrypt hash in PostgreSQL and never stores the plaintext password. Then sign in and open `/user-admin` to create accounts for existing catalog users. Each account email must match that user's catalog profile email. Use `/rbac` to assign roles and fine-grained permissions.

The initial RBAC administrator is represented by `user:default/admin` in `examples/org.yaml`. Configure `LOCAL_AUTH_ADMIN_PASSWORD` in the production secret environment before deployment. The bootstrap password is used only when the admin account does not already exist; changing it later does not reset an existing password.

### Pipelines

Open `/pipelines` to select Azure DevOps, GitHub Actions, or GitLab CI, choose a project/repository and pipeline, and run it on a branch. Azure DevOps organizations are listed from `AZURE_DEVOPS_ORGS` (comma-separated). Runs are polled; when a run completes, the agent analyzes its logs and a bounded set of source files from the exact run commit. Grant `pipelines.read`, `pipelines.run`, and `pipelines.analyze` through `/rbac` as needed.

The agent requires an OpenAI-compatible chat-completions URL, model, and API key in `.env`. For Azure OpenAI, set the endpoint to the full deployment chat-completions URL including `api-version`, and set the API key header to `api-key` with an empty prefix. Logs and source are treated as untrusted input and common token patterns are redacted before analysis.

A build is marked certified only when the pipeline succeeds and successful jobs whose names include Sonar, Trivy, and Checkmarx are all present. Configure those pipeline jobs to enforce the organization’s vulnerability thresholds; a successful scan job alone is not a substitute for a scanner quality gate. The AI report cannot override this deterministic certification check.

### Infrastructure Agent

Open `/infra-agent` to transform plain-text cloud requirements into Terraform infrastructure deployments using only your approved Terraform modules (located in `../terraform-modules`).

- **Architecture:**
  - **Requirement Parser:** Extracts structured requirements from natural language.
  - **Module Catalog & Policy Engine:** Enforces approved module usage, location whitelisting, required tags, and resource quotas.
  - **Generator & Reviewer Loop:** Generates deterministic JSON module calls and conducts multi-round reflection checks.
  - **Static Validation & Plan:** Runs `terraform fmt`, `init`, `validate`, `tflint`, and security scanning (`checkov`/`trivy`/`tfsec`), then creates an execution plan.
  - **Human Approval Gate:** Requires an HMAC-signed single-use token tied to the plan's exact SHA-256 hash before running `terraform apply`.
- **Portal Interface:**
  - Submit new infrastructure requests via YAML or JSON with instant schema validation.
  - Inspect plan summaries, resource additions/changes/destructions, and generated `.tf` files.
  - Issue human approval tokens and trigger deployments directly from the UI.
  - Search and explore the catalog of 36 approved HashiCorp Azure modules with inputs, types, and outputs.
- **Local CLI Usage:**
  ```bash
  cd infra-agent
  source .venv/bin/activate
  python -m agent.main check-llm
  python -m agent.main validate-request -f examples/request.example.yaml
  python -m agent.main run -f my-request.yaml
  python -m agent.main approve <request-id> --approver "Admin"
  python -m agent.main apply <request-id> --token <token>
  ```
- **RBAC Permissions:** Grant `infraagent.read`, `infraagent.run`, `infraagent.approve`, and `infraagent.apply` via `/rbac`.

The frontend is served at `http://localhost:3000`; the backend API is at `http://localhost:7007`. See `DOCKER_USAGE.md` for the full Compose workflow and container-specific instructions.

## Configuration

Copy `.env.example` to `.env` and provide only the credentials and settings needed for your environment. Compose loads this file into the backend process environment; `.env` is excluded from Docker build context and is not copied into the image. Do not commit `.env` or place secrets in Dockerfiles.

| Variable                                                                | Purpose                                                                                                                                                                                         |
| ----------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `POSTGRES_PASSWORD`                                                     | PostgreSQL password used by the backend and Compose database                                                                                                                                    |
| `APP_BASE_URL`                                                          | Public frontend URL used by browser clients and authentication callbacks; set this to the VM's reachable frontend URL for remote access                                                         |
| `LOCAL_AUTH_ADMIN_PASSWORD`                                              | Initial local admin password; use a unique secret of at least 12 characters                                                                                                                    |
| `GITHUB_TOKEN`, `GITHUB_ORG`                                            | GitHub repository/catalog integration and organization view                                                                                                                                     |
| `GITLAB_HOST`, `GITLAB_BASE_URL`, `GITLAB_API_BASE_URL`, `GITLAB_TOKEN` | GitLab integration and repository publishing; set the hostname without a scheme, the instance URL (including `http://` or `https://`), its API v4 URL, and an appropriately scoped access token |
| `KDASHM_AGENT_MODELS_FILE`, `KDASHM_AGENT_MODELS_JSON`                   | Optional model configuration file path (recommended) or inline JSON for the Kubernetes assistant                                                                                               |
| `AZURE_DEVOPS_ORGS`, `AZURE_DEVOPS_PAT`                                  | Comma-separated Azure DevOps organizations and a PAT with project/build read and pipeline execution permissions                                                                                |
| `PIPELINE_AGENT_ENDPOINT`, `PIPELINE_AGENT_MODEL`, `PIPELINE_AGENT_API_KEY` | Full OpenAI-compatible chat-completions endpoint, deployment/model name, and secret API key                                                                                                    |
| `PIPELINE_AGENT_API_KEY_HEADER`, `PIPELINE_AGENT_API_KEY_PREFIX`         | Optional auth header and prefix; use `api-key` and an empty prefix for Azure OpenAI                                                                                                              |
| `OLLAMA_BASE_URL`                                                       | Ollama endpoint for the Infra Agent (e.g., `http://localhost:11434`), when using the Ollama provider                                                                                            |
| `AZURE_FOUNDRY_ENDPOINT`, `AZURE_FOUNDRY_API_KEY`                       | Azure AI Foundry endpoint and API key for the Infra Agent, when using the Azure Foundry provider                                                                                                 |
| `ARM_SUBSCRIPTION_ID`                                                   | Target Azure Subscription GUID for Terraform provisioning (if omitted from the request)                                                                                                         |
| `INFRA_AGENT_APPROVAL_SECRET`                                           | HMAC secret (≥32 chars) for signing single-use human approval tokens                                                                                                                             |
| `INFRA_AGENT_DIR`                                                       | Custom path to `infra-agent` directory (defaults to `./infra-agent` within the repository)                                                                                                       |

GitHub, GitLab, Azure, and model features require their corresponding provider credentials. Microsoft sign-in is not enabled by default. In Compose, backend `env_file` injection supplies values at runtime; it does not mount the `.env` file into the container.

For the Kubernetes assistant, copy `kdashm-agent-models.example.json` to `kdashm-agent-models.json` (PowerShell: `Copy-Item kdashm-agent-models.example.json kdashm-agent-models.json`), then replace the placeholder endpoint, deployment name, and key. The local JSON file is git-ignored and excluded from Docker build context. The Compose files mount it read-only into the backend; the local development `.env` points to it from the repository root. `API_ENDPOINT` must be the base URL for an Azure OpenAI-compatible deployment (for example, `https://<resource-name>.openai.azure.com`), and `MODEL_NAME` is that deployment's name. The backend appends `/openai/deployments/{MODEL_NAME}/chat/completions` and sends `ACCESS_KEY` as the `api-key` header. A Foundry project's generic `/models/chat/completions` endpoint is not supported by this current request implementation. The assistant reads cluster summaries and warning events to provide analysis and suggested fixes; it does not apply fixes automatically.

For a remote HTTP development deployment, set `APP_BASE_URL` in `.env` to the VM's reachable frontend URL (for example, `http://<vm-ip>:3000`) before building. The frontend's Nginx proxy forwards `/api` requests to the backend over the private Compose network; the backend port is not published on the VM. Frontend configuration is embedded in the static bundle, so changing `APP_BASE_URL` requires rebuilding the frontend image.

GitLab scaffolder templates list their permitted host and namespace in each template's `RepoUrlPicker` `ui:options.allowedHosts` and `allowedOwners`. Keep these in sync with `GITLAB_HOST` in `.env` and the GitLab group used for publishing; otherwise the repository location picker may omit the intended host or namespace. For self-hosted GitLab, set its `external_url` to an address reachable by the Backstage backend and keep it aligned with `GITLAB_BASE_URL`; GitLab uses `external_url` to generate clone URLs, so a private or otherwise unreachable advertised address causes publishing to fail even when API access works. The VM bootstrap gets the current Application Gateway frontend URL from Terraform, rather than assuming a fixed public IP. Backstage's `.env` and template `allowedHosts` still need to match the gateway address; a DNS name mapped to the gateway is preferable if its address can change.

## Kubernetes and Azure Runtime Notes

The Kubernetes dashboard and Azure routes invoke `kubectl`, `helm`, and `az` as local backend processes. They need the corresponding CLI executables, credentials, and configuration to be available to the backend runtime. The Compose files do not automatically mount your kubeconfig or Azure CLI login, and the Dockerfiles do not install these tools. Configure them explicitly for container use, or run the backend in an environment where they are already available.

Kubernetes actions use the active kubeconfig context and the backend's Kubernetes identity. Some actions modify or delete cluster resources, so use appropriate credentials and RBAC, especially outside development clusters.

## Build and Tests

Common repository scripts:

```bash
yarn tsc                 # TypeScript check
yarn build:backend       # Build backend
yarn build:all           # Build all workspaces
yarn test                # Run repository tests
yarn test:all            # Run tests with coverage
yarn test:e2e            # Run Playwright end-to-end tests
yarn lint:all            # Lint all workspaces
yarn prettier:check      # Check formatting
```

The Playwright configuration starts the frontend and backend when they are not already running. End-to-end tests may still require the configured database and other runtime prerequisites.

## Container Deployment

Build the backend and frontend artifacts first, then start one Compose stack from the repository root:

```powershell
yarn install --immutable
yarn tsc
yarn build:backend
yarn workspace app build
docker compose -f docker-compose.linux.yml up --build
```

For Windows containers, enable Windows container mode and use `docker-compose.windows.yml`. Both stacks expose the frontend on port `3000`, the backend on `7007`, and PostgreSQL on `5432`. Persistent database data is stored in a named volume. Use `docker compose ... down -v` only when you intend to delete that data.
