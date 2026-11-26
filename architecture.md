# Backstage Skynet — Architecture Document

> **Project:** `backstage-skynet` · **Branch:** `integration/infra-agent`
> **Root:** `/Users/akashnadimpally/Desktop/Beta/LOCAL/backstage-skynet`

---

## Table of Contents

1. [System Layers Overview](#1-system-layers-overview)
2. [Frontend — Portal Routes](#2-frontend--portal-routes)
3. [Backend — Plugin Map](#3-backend--plugin-map)
4. [External Integrations](#4-external-integrations)
5. [Infra-Agent — Internal Pipeline](#5-infra-agent--internal-pipeline)
6. [Auth Flows](#6-auth-flows)
7. [RBAC Permissions](#7-rbac-permissions)
8. [Storage & Persistence](#8-storage--persistence)
9. [End-to-End Flow — IaC Request Lifecycle](#9-end-to-end-flow--iac-request-lifecycle)
10. [Directory Layout](#10-directory-layout)
11. [Runtime Dependencies](#11-runtime-dependencies)

---

## 1. System Layers Overview

```mermaid
flowchart TD
    A["🌐 Browser\nReact SPA — :3000"]
    B["⚙️ Backstage Backend\nNode.js / Express — :7007"]
    C["🐍 Infra Agent\nPython subprocess"]
    D["☁️ External Services\nAzure · GitHub · GitLab · K8s"]
    E["🤖 LLM Providers\nOllama · Azure AI Foundry"]
    F["🏗️ Terraform\ntf fmt / init / validate / plan / apply"]
    G[("💾 PostgreSQL\n:5432")]
    H["📁 File System\ninfra-agent/generated/"]

    A -->|"REST /api/*"| B
    B -->|"subprocess"| C
    B -->|"API calls"| D
    C -->|"prompts"| E
    C -->|"CLI"| F
    F -->|"ARM credentials"| D
    B --> G
    C --> H
```

---

## 2. Frontend — Portal Routes

The React SPA is served at `:3000`. Each route maps to a module file inside `packages/app/src/modules/`.

```mermaid
flowchart LR
    NAV["Sidebar\nnav/Sidebar.tsx"]

    NAV --> HOME["/\nPortal Home\nportal-home.tsx"]
    NAV --> CAT["/catalog\nBackstage Catalog\nBuilt-in plugin"]
    NAV --> SCAF["/scaffolder\nProject Scaffolder\nBuilt-in plugin"]
    NAV --> GHORG["/github-org\nGitHub Org Browser\nBuilt-in plugin"]
    NAV --> KDASHM["/kdashm\nKubernetes Dashboard\nkdashm*.tsx"]
    NAV --> CLOUD["/cloud\nAzure Cost & Subscriptions\ncloud.tsx"]
    NAV --> USERS["/user-admin\nLocal User Management\nlocal-users.tsx"]
    NAV --> PIPES["/pipelines\nCI/CD Pipelines\npipelines.tsx"]
    NAV --> INFRA["/infra-agent\nIaC Automation\ninfra-agent.tsx"]
    NAV --> RBAC["/rbac\nRole Management\nBuilt-in plugin"]
    NAV --> SETTINGS["/settings\nUser Settings\nBuilt-in plugin"]
    NAV --> NOTIF["/notifications\nNotifications\nBuilt-in plugin"]
    NAV --> SEARCH["/search\nGlobal Search\nBuilt-in plugin"]
```

> [!NOTE]
> `portal-home.tsx` acts as the landing dashboard showing 5 plugin cards: **Kubernetes**, **Cloud**, **Pipelines**, **User Admin**, and **Infra Agent**.

---

## 3. Backend — Plugin Map

All plugins are registered in `packages/backend/src/index.ts`. The backend runs on `:7007`.

### 3a. Standard Backstage Plugins

```mermaid
flowchart LR
    IDX["index.ts\nPlugin Registry"]

    IDX --> SB1["app-backend\nServes frontend build"]
    IDX --> SB2["catalog\nSoftware catalog + org"]
    IDX --> SB3["scaffolder\nTemplate engine\nGitHub + GitLab publish"]
    IDX --> SB4["auth\nGitHub OAuth\nGitLab OAuth"]
    IDX --> SB5["techdocs\nMarkdown docs"]
    IDX --> SB6["search\nFull-text search via PG"]
    IDX --> SB7["kubernetes\nCluster integration"]
    IDX --> SB8["permission / RBAC\nPolicy enforcement"]
    IDX --> SB9["notifications + signals\nReal-time events"]
    IDX --> SB10["proxy\nConfigured routes"]
    IDX --> SB11["user-settings\nPer-user preferences"]
    IDX --> SB12["mcp-actions\nAI action handlers"]
```

### 3b. Custom Backend Routers

```mermaid
flowchart TD
    IDX["index.ts"]

    IDX --> RC["cloud-auth.ts\n/api/cloud"]
    IDX --> RK["kdashm-router.ts\n/api/kdashm"]
    IDX --> RP["pipelines-router.ts\n/api/pipelines"]
    IDX --> RI["infra-agent-router.ts\n/api/infra-agent"]

    RC --> RC1["GET /api/cloud/login\nAzure device-code MSAL"]
    RC --> RC2["GET /api/cloud/subscriptions\nList Azure subscriptions"]
    RC --> RC3["GET /api/cloud/costs\nAzure cost data"]
    RC --> RC4["GET /api/cloud/resource-groups\nList resource groups"]

    RK --> RK1["GET /api/kdashm/clusters\nAKS cluster list"]
    RK --> RK2["POST /api/kdashm/kubectl\nkubectl exec via kdashm-k8s.ts"]
    RK --> RK3["POST /api/kdashm/helm\nhelm exec"]

    RP --> RP1["GET /api/pipelines\nAzure DevOps + GitHub + GitLab"]
    RP --> RP2["POST /api/pipelines/:id/trigger\nTrigger pipeline run"]

    RI --> RI1["GET /api/infra-agent/status\nAgent health check"]
    RI --> RI2["GET /api/infra-agent/catalog\nList 36 Terraform modules"]
    RI --> RI3["POST /api/infra-agent/requests\nSubmit IaC requirement"]
    RI --> RI4["GET /api/infra-agent/requests\nList all requests + status"]
    RI --> RI5["POST /api/infra-agent/requests/:id/approve\nHMAC approval"]
    RI --> RI6["POST /api/infra-agent/requests/:id/apply\nTrigger apply"]
    RI --> RI7["POST /api/infra-agent/validate\nValidate request YAML"]
```

---

## 4. External Integrations

```mermaid
flowchart LR
    subgraph AZURE["Azure Platform"]
        AZ1["ARM REST API\nSubscriptions · Resource Groups"]
        AZ2["Azure Cost Management API"]
        AZ3["Azure DevOps API\nPipelines list + trigger"]
        AZ4["AKS Clusters\nkubeconfig discovery"]
        AZ5["Azure AI Foundry\nCloud LLM endpoint"]
    end

    subgraph SOURCE["Source Control"]
        GH["GitHub API\nOrg browser · PR board\nScaffolder publish · Catalog"]
        GL["GitLab API\nRepo publish · Scaffolder · Catalog"]
    end

    subgraph IaC["Infrastructure-as-Code"]
        TF["Terraform Binary\nfmt · init · validate\nplan · apply"]
        TFL["tflint\nHCL linting"]
        SEC["checkov / trivy / tfsec\nSecurity scanning"]
        MOD["../terraform-modules\n36 HashiCorp Azure modules\nsibling repo"]
    end

    subgraph LLM["LLM Providers"]
        OLL["Ollama :11434\nLocal LLM"]
        FDY["Azure AI Foundry\nCloud LLM"]
    end

    DB[("PostgreSQL :5432\ncatalog · search · techdocs\nnotifications · user-settings")]
```

---

## 5. Infra-Agent — Internal Pipeline

The infra-agent is a Python 3.14 service inside `infra-agent/`. It is invoked as a subprocess by `infra-agent-router.ts`.

### 5a. Module Map

```mermaid
flowchart TD
    subgraph ENTRY["Entry Point"]
        MAIN["main.py\nCLI — run / approve / apply\n--json --yes flags"]
    end

    subgraph ORCHESTRATION["Orchestration"]
        PIPE["pipeline.py\nMain workflow controller"]
        SET["settings.py\nPydantic config from config.yaml"]
    end

    subgraph DATA["Data & Contracts"]
        MOD["models.py\nPydantic models"]
        CAT["module_catalog.py\ncatalog.yaml — 36 modules\ntype checker"]
        POL["policy.py\nPlan + config enforcement"]
        SCH["schemas/\nJSON schema files"]
    end

    subgraph AI["AI Agents"]
        PAR["requirements_parser.py\nAgent 0 — Extract intent from text"]
        GEN["generator.py\nAgent 1 — Terraform JSON generation"]
        REV["reviewer.py\nAgent 2 — Reflection + feedback"]
        REP["repair.py\nAgent 3 — Error repair"]
        LLM["llm.py\nProvider-neutral LLM client"]
        OC["ollama_client.py"]
        FC["foundry_client.py"]
        PRM["prompts/\nparser.txt · generator.txt\nreviewer.txt · repair.txt"]
    end

    subgraph BUILD["Build & Execute"]
        BLD["builder.py\nDeterministic HCL file builder"]
        EXE["executor.py\ntf fmt · init · validate\ntflint · security scan\nplan · apply"]
    end

    subgraph OPS["Operations"]
        APR["approval.py\nHMAC token issuance + verify"]
        STO["storage.py\nPer-request artifact storage"]
    end

    MAIN --> PIPE
    SET --> PIPE
    PIPE --> PAR --> LLM
    PIPE --> CAT
    PIPE --> POL
    PAR --> GEN --> LLM
    GEN --> REV --> LLM
    REV -->|"feedback loop"| GEN
    GEN --> BLD --> EXE
    EXE -->|"on error"| REP --> LLM
    REP -->|"repaired config"| GEN
    EXE --> APR
    PIPE --> STO
    LLM --> OC
    LLM --> FC
    PRM --> LLM
```

### 5b. Pipeline Stages (Linear View)

```mermaid
flowchart LR
    S1["📝 Parse\nrequirements_parser\nLLM extracts intent"]
    S2["📦 Catalog Check\nmodule_catalog\nValidate module types"]
    S3["📏 Policy Check\npolicy\nEnforce constraints"]
    S4["⚙️ Generate\ngenerator LLM\nTerraform JSON"]
    S5["🔍 Review\nreviewer LLM\nReflection feedback"]
    S6["🔨 Build\nbuilder\nHCL files"]
    S7["✅ Validate\nexecutor\ntf fmt/init/validate\ntflint / security"]
    S8["🗺️ Plan\ntf plan\nChange preview"]
    S9["🔐 Approve\nHMAC token gate\nPortal approval"]
    S10["🚀 Apply\ntf apply\nAzure resources deployed"]

    S1 --> S2 --> S3 --> S4 --> S5
    S5 -->|"needs change"| S4
    S5 -->|"approved"| S6 --> S7
    S7 -->|"error"| REPAIR["🛠️ Repair\nrepair LLM"]
    REPAIR --> S4
    S7 -->|"valid"| S8 --> S9 --> S10
```

---

## 6. Auth Flows

```mermaid
flowchart TD
    subgraph LOCAL["Local Authentication"]
        LA["User submits\nusername + password"]
        LB["local-auth.ts\nscrypt PBKDF2 hash verify"]
        LC["JWT session issued"]
        LA --> LB --> LC
    end

    subgraph AZURE_AUTH["Azure Authentication"]
        AA["User clicks\nSign in with Azure"]
        AB["cloud-auth.ts\nMSAL device-code flow"]
        AC["Browser shows\ndevice code URL"]
        AD["ARM token stored\nin backend session"]
        AA --> AB --> AC --> AD
    end

    subgraph GH_AUTH["GitHub OAuth"]
        GA["User clicks\nSign in with GitHub"]
        GB["Backstage auth plugin\nOAuth 2.0 redirect"]
        GC["GitHub token\nstored in session"]
        GA --> GB --> GC
    end

    subgraph GL_AUTH["GitLab OAuth"]
        GLA["User clicks\nSign in with GitLab"]
        GLB["Backstage auth plugin\nOAuth 2.0 redirect"]
        GLC["GitLab token\nstored in session"]
        GLA --> GLB --> GLC
    end

    subgraph HMAC_AUTH["Infra Approval Token"]
        HA["infra-agent-router\nreceives approve request"]
        HB["approval.py\nHMAC-SHA256 token generated"]
        HC["Token sent to portal\nuser confirms in UI"]
        HD["approval.py\nverifies HMAC before apply"]
        HA --> HB --> HC --> HD
    end
```

---

## 7. RBAC Permissions

```mermaid
flowchart LR
    USER["Authenticated\nUser"]

    USER --> RBAC["permission / RBAC\nBackstage Permission Framework"]

    subgraph K8S_PERMS["Kubernetes Permissions"]
        K1["kdashm.cluster.read\nView clusters + pods"]
        K2["kdashm.cluster.write\nkubectl / helm commands"]
        K3["kdashm.exec.run\nExecute kubectl exec"]
    end

    subgraph PIPE_PERMS["Pipeline Permissions"]
        P1["pipelines.pipeline.read\nList pipelines + runs"]
        P2["pipelines.pipeline.trigger\nTrigger pipeline run"]
    end

    subgraph INFRA_PERMS["Infra Agent Permissions"]
        I1["infraagent.read\nView requests + catalog"]
        I2["infraagent.run\nSubmit new IaC request"]
        I3["infraagent.approve\nApprove pending plan"]
        I4["infraagent.apply\nTrigger terraform apply"]
    end

    subgraph STD_PERMS["Standard Backstage Permissions"]
        S1["catalog.entity.*\nRead / write entities"]
        S2["scaffolder.task.*\nCreate / cancel tasks"]
        S3["techdocs.doc.*\nView / build docs"]
    end

    RBAC --> K8S_PERMS
    RBAC --> PIPE_PERMS
    RBAC --> INFRA_PERMS
    RBAC --> STD_PERMS
```

---

## 8. Storage & Persistence

```mermaid
flowchart TD
    subgraph PG["PostgreSQL :5432"]
        T1["catalog\nEntities + relations"]
        T2["search\nFull-text index"]
        T3["techdocs\nDoc builds"]
        T4["notifications\nEvent log"]
        T5["user_settings\nPer-user preferences"]
    end

    subgraph FS["File System — infra-agent/"]
        G1["generated/requests/\n  request-id/\n    config.tf.json\n    main.tf\n    plan.tfplan\n    plan.json\n    apply.log\n    audit.jsonl"]
        G2["module-catalog/catalog.yaml\n36 approved Azure modules"]
        G3["prompts/\nparser · generator\nreviewer · repair"]
    end

    subgraph LOCAL_FS["Other Local Files"]
        L1["kdashm-agent-models.json\nAI model config for K8s panel"]
    end

    BACKEND["Backend"] --> PG
    INFRA["infra-agent"] --> FS
    KDASHM["kdashm plugin"] --> L1
```

---

## 9. End-to-End Flow — IaC Request Lifecycle

```mermaid
sequenceDiagram
    actor U as 👤 User
    participant FE as Portal\ninfra-agent.tsx
    participant BE as Backend\ninfra-agent-router.ts
    participant PY as Python Agent\npipeline.py
    participant LLM as LLM\nOllama / Foundry
    participant TF as Terraform\nBinary
    participant AZ as Azure\nCloud

    U->>FE: Type infra requirement in natural language
    FE->>BE: POST /api/infra-agent/requests
    BE->>PY: spawn — python -m agent.main run --json

    Note over PY,LLM: Stage 1 — Parse Requirements
    PY->>LLM: Send requirement text + parser prompt
    LLM-->>PY: Return structured intent JSON

    Note over PY: Stage 2 — Catalog + Policy Validation
    PY->>PY: Check requested modules against catalog.yaml
    PY->>PY: Apply policy constraints

    Note over PY,LLM: Stage 3 — Generate + Review Loop
    PY->>LLM: Generator prompt → Terraform JSON config
    LLM-->>PY: Generated config
    PY->>LLM: Reviewer prompt → feedback
    LLM-->>PY: Review result
    alt Review requests change
        PY->>LLM: Re-generate with feedback
        LLM-->>PY: Revised config
    end
    PY->>PY: builder.py — write config.tf.json + main.tf

    Note over PY,TF: Stage 4 — Static Validation
    PY->>TF: terraform fmt
    PY->>TF: terraform init
    PY->>TF: terraform validate
    PY->>TF: tflint + checkov / tfsec
    alt Validation error
        TF-->>PY: Error output
        PY->>LLM: Repair prompt + error
        LLM-->>PY: Repaired config
        PY->>TF: Re-validate
    end

    Note over PY,TF: Stage 5 — Plan
    PY->>TF: terraform plan -out plan.tfplan
    TF-->>PY: Plan JSON (change summary)
    PY->>PY: Plan policy check

    Note over BE,U: Stage 6 — Approval Gate
    PY-->>BE: status = awaiting_approval + plan summary
    BE-->>FE: Polling response — plan diff + approve button
    FE-->>U: Show change plan for review
    U->>FE: Click Approve
    FE->>BE: POST /api/infra-agent/requests/:id/approve
    BE->>PY: spawn — python -m agent.main approve --json (HMAC token)
    PY->>PY: approval.py — verify HMAC token

    Note over PY,AZ: Stage 7 — Apply
    PY->>TF: terraform apply plan.tfplan
    TF->>AZ: Provision Azure resources via ARM
    AZ-->>TF: Resources created
    TF-->>PY: Apply output + resource IDs
    PY->>PY: Write audit.jsonl — full run record
    PY-->>BE: Exit 0 — result JSON
    BE-->>FE: status = applied
    FE-->>U: ✅ Infrastructure deployed successfully
```

---

## 10. Directory Layout

```
backstage-skynet/
│
├── packages/
│   ├── app/                              # Frontend (React + Backstage UI)
│   │   └── src/
│   │       ├── App.tsx                   # Route registration
│   │       └── modules/
│   │           ├── portal-home.tsx       # / — Landing dashboard
│   │           ├── auth.tsx              # Auth page
│   │           ├── cloud.tsx             # /cloud — Azure cost & subs
│   │           ├── kdashm-home.tsx       # /kdashm — K8s dashboard home
│   │           ├── kdashm-agent-panel.tsx# K8s AI assistant panel
│   │           ├── kdashm-sidebar.tsx    # K8s sidebar nav
│   │           ├── kdashm.tsx            # K8s main view
│   │           ├── local-users.tsx       # /user-admin
│   │           ├── pipelines.tsx         # /pipelines — CI/CD aggregation
│   │           ├── infra-agent.tsx       # /infra-agent — IaC automation
│   │           └── nav/
│   │               ├── Sidebar.tsx       # Global sidebar navigation
│   │               └── SidebarLogo.tsx   # Branding
│   │
│   └── backend/                          # Backend (Node.js + Express)
│       └── src/
│           ├── index.ts                  # Plugin registration entrypoint
│           ├── cloud-auth.ts             # Azure MSAL + cost APIs
│           ├── kdashm-router.ts          # K8s REST routes
│           ├── kdashm-k8s.ts             # kubectl / helm subprocess exec
│           ├── kubernetes-aks-discovery.ts
│           ├── pipelines-router.ts       # Azure DevOps + GitHub + GitLab
│           ├── local-auth.ts             # Scrypt local auth provider
│           └── infra-agent-router.ts     # Infra Agent REST bridge
│
├── infra-agent/                          # Python IaC automation service
│   ├── agent/
│   │   ├── main.py                       # CLI entrypoint (run/approve/apply)
│   │   ├── pipeline.py                   # Workflow orchestrator
│   │   ├── settings.py                   # Pydantic config loader
│   │   ├── models.py                     # Pydantic data contracts
│   │   ├── module_catalog.py             # 36-module catalog + type check
│   │   ├── policy.py                     # Plan + config policy enforcement
│   │   ├── requirements_parser.py        # LLM: intent extraction
│   │   ├── generator.py                  # LLM Agent 1: Terraform JSON
│   │   ├── reviewer.py                   # LLM Agent 2: Reflection
│   │   ├── repair.py                     # LLM Agent 3: Error repair
│   │   ├── builder.py                    # Deterministic HCL builder
│   │   ├── executor.py                   # Terraform CLI execution
│   │   ├── approval.py                   # HMAC token issuance + verify
│   │   ├── storage.py                    # Per-request artifact storage
│   │   ├── llm.py                        # Provider-neutral LLM client
│   │   ├── ollama_client.py              # Ollama provider
│   │   ├── foundry_client.py             # Azure AI Foundry provider
│   │   └── prompts/
│   │       ├── parser.txt
│   │       ├── generator.txt
│   │       ├── reviewer.txt
│   │       └── repair.txt
│   ├── config.yaml                       # Agent configuration
│   ├── module-catalog/catalog.yaml       # 36 approved Azure modules
│   ├── schemas/                          # Pydantic JSON schema files
│   ├── tests/                            # 76 pytest tests (all passing ✅)
│   └── .venv/                            # Python 3.14 virtual environment
│
├── templates/                            # 22 Backstage scaffolder templates
├── examples/                             # Catalog YAML + org.yaml
├── app-config.yaml                       # Development configuration
├── app-config.production.yaml            # Production configuration
├── docker-compose.linux.yml              # Linux Docker Compose
├── docker-compose.windows.yml            # Windows Docker Compose
├── .env.example                          # All required environment variables
└── README.md                             # Project documentation
```

> [!TIP]
> **Sibling repo** `../terraform-modules` (36 HashiCorp Azure modules) lives outside this repo. It is referenced by `infra-agent/config.yaml` via `modules.root: ../../terraform-modules/develop/modules/azure/hashicorp`. It is **not embedded**.

---

## 11. Runtime Dependencies

| Component | Technology | Version | Transport |
|---|---|---|---|
| Frontend SPA | React + Backstage UI | Backstage latest | Browser — `:3000` |
| Backend API | Node.js + Express | Node 20+ | HTTP — `:7007` |
| Infra Agent | Python 3.14 | 3.14 | stdio JSON subprocess |
| Database | PostgreSQL | 14+ | TCP — `:5432` |
| Local LLM | Ollama | Latest | HTTP — `:11434` |
| Cloud LLM | Azure AI Foundry | — | HTTPS |
| IaC Runner | Terraform | ≥ 1.5 | CLI process |
| Linting | tflint | Latest | CLI process |
| Security Scan | checkov / trivy / tfsec | Latest | CLI process |
| Source Control | GitHub REST API v3 | — | HTTPS |
| Source Control | GitLab REST API v4 | — | HTTPS |
| Azure Platform | ARM REST API + MSAL | — | HTTPS |
| Azure CI/CD | Azure DevOps REST API | — | HTTPS |
| Kubernetes | kubectl + kubeconfig | — | kubeconfig clusters |
| Container Runtime | Docker + Docker Compose | — | docker socket |

### Environment Variables Required

| Variable | Used By | Purpose |
|---|---|---|
| `POSTGRES_*` | Backstage backend | Database connection |
| `GITHUB_TOKEN` | Backend auth + scaffolder | GitHub integration |
| `GITLAB_TOKEN` | Backend auth + scaffolder | GitLab integration |
| `AZURE_CLIENT_ID` | cloud-auth.ts | Azure MSAL app ID |
| `ARM_SUBSCRIPTION_ID` | infra-agent | Terraform Azure target |
| `ARM_CLIENT_ID` | infra-agent | Service principal |
| `ARM_CLIENT_SECRET` | infra-agent | Service principal secret |
| `ARM_TENANT_ID` | infra-agent | Azure AD tenant |
| `OLLAMA_BASE_URL` | infra-agent llm.py | Local LLM endpoint |
| `AZURE_FOUNDRY_ENDPOINT` | infra-agent llm.py | Cloud LLM endpoint |
| `AZURE_FOUNDRY_API_KEY` | infra-agent llm.py | Cloud LLM API key |
| `INFRA_AGENT_APPROVAL_SECRET` | approval.py | HMAC signing secret |
| `INFRA_AGENT_DIR` | infra-agent-router.ts | Python service path |
