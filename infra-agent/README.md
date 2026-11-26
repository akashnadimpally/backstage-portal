# infra-agent

An Azure infrastructure agent that turns a plain-text request into a Terraform deployment
**using only your approved Terraform modules**, with local LLMs (Ollama) for reasoning and
deterministic code for everything that must be safe.

```
User request (YAML/JSON)
  -> Pydantic validation (rejects bad input before any LLM call)
  -> Requirement Parser LLM ............ structured JSON: which catalog modules + details
  -> Module catalog + policy engine .... unknown / disallowed modules rejected
  -> Agent 1: Generator LLM ............ JSON module calls (never HCL)
       <-> deterministic checks ........ inputs, types, references, cycles, policy (self-correction loop)
  -> Deterministic file builder ........ providers.tf, main.tf, variables.tf, outputs.tf, terraform.tfvars.json
  -> Agent 2: Reviewer LLM ............. APPROVED / REJECTED + findings (reflection loop with Generator)
  -> Static validation controller ...... fmt -> init -> validate -> tflint -> security scan
  -> terraform plan -out=tfplan ........ plan.json
       on failure: classify -> Agent 3: Repair LLM (only attributable code errors, max 5 attempts)
  -> Plan policy check ................. destroys/replaces, providers, locations, required tags, limits
  -> Human approval gate ............... single-use HMAC token bound to the exact tfplan hash
  -> terraform apply tfplan ............ apply failures: classify -> repair -> re-plan -> NEW approval
  -> Deployment result (result.json, outputs; sensitive values redacted)
```

## Setup

This service is integrated within the `backstage-skynet` portal under `infra-agent/`.

```bash
# From backstage-skynet/infra-agent:
cd infra-agent
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Run test suite
pytest tests/

# Rebuild module catalog if modules change (points to ../../terraform-modules)
python -m agent.main catalog build
```

PowerShell:
```powershell
cd infra-agent
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python -m pytest tests/
.\.venv\Scripts\python -m agent.main catalog build
```

Environment variables:

| Variable | Purpose |
|---|---|
| `OLLAMA_BASE_URL` | Ollama endpoint, e.g. `http://10.0.0.5:11434` (overrides `ollama.base_url`). |
| `AZURE_FOUNDRY_ENDPOINT` | Azure AI Foundry endpoint (overrides `azure_foundry.endpoint`). |
| `AZURE_FOUNDRY_API_KEY` | Only when `azure_foundry.auth: api_key` (name configurable via `api_key_env`). |
| `ARM_SUBSCRIPTION_ID` | Target subscription (unless `subscription_id` is in the request). |
| `INFRA_AGENT_APPROVAL_SECRET` | ≥32-char secret used to sign/verify approval tokens. Required for `approve`/`apply`. |
| Azure auth | Standard azurerm auth: `az login`, or `ARM_CLIENT_ID` / `ARM_CLIENT_SECRET` / `ARM_TENANT_ID`, or managed identity. |

### Choosing the LLM provider

`config.yaml` has two provider sections. Set `enabled: true` on **exactly one** of them (the agent refuses
to start otherwise). `models` maps each agent role (parser, generator, reviewer, repair) to a model.

| | `ollama` | `azure_foundry` |
|---|---|---|
| `models` values | Ollama model names (`llama3.2`, `gpt-oss:120b`, …) | **Deployment names** in your Foundry resource |
| Endpoint | `base_url` | `endpoint` (`https://<res>.services.ai.azure.com` or `https://<res>.openai.azure.com`); `/openai/v1/chat/completions` is appended |
| Auth | none | `entra_id` (DefaultAzureCredential: `az login`, managed identity, service principal env vars; needs the *Cognitive Services OpenAI User* / *Azure AI User* role) or `api_key` |
| Structured output | `format: <json schema>` | `response_format: json_schema` (falls back to `json_object` automatically if a model doesn't support it) |

Set a role's `temperature` to `null` for models that reject it (reasoning models); the Foundry client also
detects this automatically. `<think>` blocks from reasoning models are stripped before JSON validation.

Optional tools: `tflint`, and `checkov`/`trivy`/`tfsec` (choose in `static_checks.security.tool`). If they are
missing they are skipped (audited) unless `required: true`.

## Usage

```powershell
$py = ".\.venv\Scripts\python"
& $py -m agent.main check-llm                                    # enabled provider connectivity + models/deployments
& $py -m agent.main validate-request -f examples\request.example.yaml   # no LLM involved
& $py -m agent.main run -f my-request.yaml                       # stops at AWAITING_APPROVAL
& $py -m agent.main status <request-id>
& $py -m agent.main approve <request-id> --approver "Jane Doe"   # interactive; prints a one-time token
& $py -m agent.main apply <request-id> --token <token>           # or $env:INFRA_AGENT_APPROVAL_TOKEN
& $py -m agent.main catalog list | catalog show <module>
& $py -m agent.main schemas                                      # re-export schemas/*.json from Pydantic
```

Request file format: see [examples/request.example.yaml](examples/request.example.yaml) and
[schemas/infrastructure-request.json](schemas/infrastructure-request.json).

## Layout

```
agent/
  main.py               CLI
  pipeline.py           orchestration, loops, approval/apply
  settings.py           config.yaml -> typed settings
  models.py             Pydantic contracts (request, LLM outputs, diagnostics, state)
  module_catalog.py     module discovery, Terraform type parser, deterministic type checks
  policy.py             requirement / config / plan policy
  requirements_parser.py  Requirement Parser LLM
  generator.py          Agent 1 - Generator
  reviewer.py           Agent 2 - Reviewer
  repair.py             Agent 3 - Repair
  classifier.py         error classification + attribution (decides what the LLM may touch)
  builder.py            deterministic HCL rendering
  executor.py           Terraform CLI + static validation controller
  approval.py           approval tokens
  storage.py            per-request artifacts + audit.jsonl
  llm.py                provider-neutral client: JSON/Pydantic validation + retries, provider factory
  ollama_client.py      Ollama /api/chat provider
  foundry_client.py     Azure AI Foundry provider (/openai/v1/chat/completions, Entra ID or API key)
  prompts/              parser.txt, generator.txt, reviewer.txt, repair.txt
module-catalog/catalog.yaml   generated; edit `enabled` / `notes` per module (preserved on rebuild)
schemas/                      JSON Schemas exported from the Pydantic models
generated/requests/<id>/      one folder per request (git-ignored; contains state and plans)
tests/                        pytest suite (fake LLM + fake executor, no network)
```

Each request folder contains `request.json`, `requirements.json`, `generated-config.json`, the `.tf` files,
`terraform.tfvars.json`, `review.json` (+ `reviews/round-N.json`), `plan.json`, `plan-summary.json`,
`errors/`, `logs/` (every command's output), `llm/` (every prompt/response), `state.json`, `audit.jsonl`
and, after apply, `result.json`.

## Backstage Integration

The Infra Agent is exposed to Backstage Skynet through:
- **Backend Plugin (`/api/infra-agent`):** Express router in `packages/backend/src/infra-agent-router.ts`.
  - `GET /api/infra-agent/status` - Checks agent availability, active LLM provider, and policy limits.
  - `GET /api/infra-agent/check-llm` - Tests model connectivity.
  - `GET /api/infra-agent/catalog` - Returns all approved Terraform modules with variables and outputs.
  - `GET /api/infra-agent/requests` - Lists past and active infrastructure requests.
  - `GET /api/infra-agent/requests/:id` - Full request details, plan diffs, generated `.tf` files, and audit events.
  - `POST /api/infra-agent/validate` - Validates YAML/JSON requests against Pydantic schema and policy.
  - `POST /api/infra-agent/requests/run` - Runs the pipeline asynchronously or synchronously.
  - `POST /api/infra-agent/requests/:id/approve` - Issues HMAC-signed single-use deployment token.
  - `POST /api/infra-agent/requests/:id/apply` - Applies the approved Terraform plan.
- **Frontend Module (`/infra-agent`):** Interactive UI in `packages/app/src/modules/infra-agent.tsx` accessible from the sidebar.


## Safety design

* **Grounded requirements.** Every component the parser selects must quote evidence from the request;
  ungrounded components / "unsupported" items are dropped (audited as `parser.grounding`), duplicates merged.
* **LLMs never write HCL.** They return JSON validated by Pydantic. The only expression they may use is a
  reference string `${module.<call>.<output>["<key>"]}` (or `${var.<name>}` for operator-supplied secrets).
  Anything else is a literal and is escaped when rendered.
* **Catalog-driven, no hard-coded resources.** Module inputs, nested object attributes, required fields,
  outputs and validation messages are read from the module source. Unknown inputs/attributes, missing
  required fields, wrong types, unknown outputs, unknown map keys and dependency cycles are caught
  before Terraform runs.
* **Deterministic injection:** the request location fills required `location` attributes and mandatory tags
  (`environment`, `managed_by`, `request_id` + request tags) are merged into every `tags` attribute.
* **Classify first.** Credentials, permissions, quota, Azure Policy, existing resources, state locks, tooling,
  lint/security findings and defects inside module source go straight to the user. Only code-level errors
  attributable to one module call (or outputs) are sent to the Repair agent, which sees only that target.
  Transient errors are retried without an LLM. After `loops.max_repair_attempts` (5) the issue is displayed.
* **Approval token:** HMAC-signed with `INFRA_AGENT_APPROVAL_SECRET`, bound to request id + SHA-256 of `tfplan`,
  expires (`approval.token_ttl_minutes`), single use. Any re-plan (e.g. after a repair) voids earlier tokens.
  Approval is interactive (typed confirmation) and is never available to an LLM.

## Notes about the current modules

* Most modules declare their own `provider "azurerm"` block, so module calls cannot use
  `for_each`/`count`/`depends_on`. The builder never emits them; ordering comes from references, and the
  executor exports `ARM_SUBSCRIPTION_ID` so those embedded provider blocks get the subscription.
* `terraform validate` currently reports a defect in `managed-identity/variables.tf`: `trim()` is called with
  one argument (Terraform requires two; `trimspace()` is probably intended). The agent classifies this as
  `module_defect` and stops for a human instead of trying to "fix" it.

## Tests

```powershell
.\.venv\Scripts\python -m pytest -q
```
