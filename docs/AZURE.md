# Azure deployment

Subscription: Azure for Students, ~$100 credit with a short remaining window. The plan spends money
where it produces portfolio evidence (eval runs, a full AI gateway for the final demo) and keeps
everything else inside free grants. Everything is Bicep + azd; nothing exists only in the portal.

## 0. Before any resource exists

1. **Credit window** — check the credit expiry date in the portal (Subscriptions → Azure for
   Students → credits). Record it at the top of `docs/MILESTONES.md`. M4 must land with ≥ 10 days
   of credit left.
2. **Model quota tier** — the plan depends on it:
   ```bash
   az rest --method get --url "https://management.azure.com/subscriptions/<SUB_ID>/providers/Microsoft.CognitiveServices/quotaTiers?api-version=2025-10-01-preview"
   ```
   Then check per-model quota for the target region in the Foundry portal (Quota page). If chat
   model quota is 0, use the provider fallback (LLM.md §11) and keep the rest of the plan.
3. **Budget alerts** — create a subscription budget of $100 with alerts at $60 and $85
   (`infra/budget.bicep`, subscription scope). Alerts don't stop spending; the per-run and per-eval
   budget guards in the app do.

## 1. Resources

| Resource | SKU / mode | Est. cost | When |
|---|---|---|---|
| Azure OpenAI (Foundry resource) + deployments | Global Standard, capacity within quota | pay per token | M0 |
| Log Analytics + Application Insights | pay-as-you-go, daily cap 0.2 GB, sampling | ~$0–2 | M4 |
| Storage account | Standard LRS; blob container `artifacts`, queue `assessments`, lifecycle 30 d | < $1 | M4 |
| Cosmos DB (NoSQL) | free tier (1000 RU/s shared DB, 25 GB) — or serverless if free tier is already used in the subscription | $0 | M4 |
| Container Apps environment | Consumption | $0 (free grant) | M4 |
| Container App `api` | 0.5 vCPU / 1 GiB, min replicas 0 | $0 | M4 |
| Container Apps Job `worker` | event-driven (queue), 1 vCPU / 2 GiB, timeout 20 min | $0 within grant | M4 |
| API Management | `Consumption` by default; `Developer` for the final ~2 weeks (param) | $0 → ~$24 | M4 / M5 |
| Azure Managed Redis | B0, RediSearch module, only if `enableRedis=true` (report Q&A semantic cache demo) | ~$12/mo | optional, M5 |
| Static Web Apps | Free | $0 | M4 |
| Container registry | GitHub Container Registry (public image) instead of ACR | $0 | M4 |

Region: one region for everything — **Poland Central**. The Azure for Students subscription has a
policy (`sys.regionrestriction`) allowing only polandcentral, austriaeast, switzerlandnorth,
italynorth and belgiumcentral; Azure OpenAI is offered only in the first, third and fourth, and all
M4 services exist in each of those except **Static Web Apps (none of the allowed regions — M4.7
needs another host)**. Poland Central is chosen for West-Europe-level pricing (checked
2026-10-06). Global Standard deployments route globally anyway. Resource group `rg-archlens-ai`
was created in swedencentral before the policy was found (groups are not restricted); its
location is metadata only, so `ai.bicep` sets the resource location explicitly.

## 2. Phase 1 — budget, then models (M0.5)

Deploy `infra/budget.bicep` (subscription scope) before anything else. Then `infra/ai.bicep` deploys the Azure OpenAI/Foundry resource and the deployments from
`config/models.yaml` (evaluator, verifier, embed; skeptic/synth reuse them). Deployment `capacity`
is in thousands of TPM; set it within the quota found in §0.2. Deploy with
`az deployment group create` (ask first). Local development then uses the v1 endpoint directly
with a key in `.env`.

## 3. Phase 2 — full stack (M4)

```
azure.yaml                     azd project (service: api, worker; host: containerapp)
infra/
  main.bicep                   params: location, apimSku, enableRedis, alertEmail, imageTag
  modules/
    identity.bicep             user-assigned MI + role assignments
    monitoring.bicep           Log Analytics, App Insights (daily cap, sampling)
    storage.bicep              account, blob container, queue, lifecycle policy
    cosmos.bicep               account (free tier or serverless), db `archlens`, containers: runs, findings, cache
    aca-env.bicep              Container Apps environment (Consumption)
    api.bicep                  Container App: ingress, min 0 replicas, secrets, env
    worker.bicep               Container Apps Job: event trigger on queue, parallelism 1
    apim.bicep                 APIM (sku param), API import, backend, policies, subscriptions
    redis.bicep                conditional on enableRedis
    swa.bicep                  Static Web App (Free)
  apim/policies/
    base.xml                   managed-identity auth to AOAI, retry, rate-limit by subscription
    developer-extras.xml       llm-token-limit + llm-emit-token-metric (non-Consumption only)
    qa-semantic-cache.xml      llm-semantic-cache-lookup/store on the Q&A operation (Redis only)
  ai.bicep                     phase 1, referenced by main.bicep as an existing resource
  budget.bicep                 subscription-scope budget + alerts
```

Key wiring:
- API: `POST /assessments` writes a `RunState` to Cosmos and a message `{run_id}` to the queue.
- Worker job: queue-triggered scale rule (prefer managed identity on the scale rule; if the target
  API version doesn't support it, use a queue-scoped SAS connection string in a job secret and
  note it in ADR form); runs `archlens worker --once`, writes artifacts to Blob, state to Cosmos.
- App → LLM: `ARCHLENS_LLM_BASE_URL` = APIM gateway URL; `ARCHLENS_LLM_KEY_HEADER` =
  `Ocp-Apim-Subscription-Key`; key stored as a Container Apps secret. APIM → Azure OpenAI uses
  APIM's managed identity (`authentication-managed-identity`, role *Cognitive Services OpenAI User*).
- App → Storage/Cosmos: user-assigned MI with data-plane roles only (SECURITY.md §7).
- Telemetry: `APPLICATIONINSIGHTS_CONNECTION_STRING` + `ARCHLENS_OTEL_ENABLED=true`.

APIM tier notes (verified against the policy reference): on **Consumption**, `llm-token-limit` and
`llm-emit-token-metric` are not supported; `llm-semantic-cache-*`, `llm-content-safety`,
`authentication-managed-identity`, `rate-limit`, `retry` and backends are. `apim.bicep` includes
`developer-extras.xml` only when `apimSku != 'Consumption'`. Token metrics always come from the app
(ARCHITECTURE §8). APIM semantic caching needs an Azure Managed Redis instance with RediSearch
enabled at creation, configured as APIM's external cache.

Switching APIM to Developer for the final weeks: `azd env set APIM_SKU Developer && azd provision`
(ask first; provisioning a classic-tier APIM takes a long time — start it early in the day).

## 4. CI/CD (GitHub Actions)

- `azd pipeline config --auth-type federated` sets up OIDC (no client secrets).
- `.github/workflows/ci.yml` — on PR/push: ruff, pyright, pytest (offline), schema drift check,
  prompt lock check, Docker build. `permissions: contents: read`.
- `.github/workflows/deploy.yml` — on push to `main` (and manual): build image, push to GHCR with
  the commit SHA tag, `azd deploy`. `permissions: id-token: write, contents: read, packages: write`.
  Deployment uses a GitHub environment `prod` with required reviewer (yourself).
- All third-party actions pinned to full commit SHAs; Dependabot for `github-actions`, `pip`
  (uv), and `docker`.
- Dockerfile: multi-stage, pinned base image digest, scanners installed at pinned versions from
  `config/tools.yaml` with checksum verification, non-root `USER`, `HEALTHCHECK` for the API image.

## 5. Cost guardrails

- Default parameters are the free/cheap ones (`apimSku=Consumption`, `enableRedis=false`).
- App Insights daily cap + adaptive sampling.
- `ARCHLENS_RUN_BUDGET_USD` per run (default $1.00) and `budget_usd` per eval config.
- Container Apps min replicas 0; worker job timeout 20 min, retry limit 1.
- Weekly check: Cost Management → cost by resource. Record spend in the milestone log.

## 6. Teardown (before the credit ends)

1. Record the demo video; export final `eval/results/` and sample reports to the repo.
2. `azd down --purge` (purges soft-deleted Cognitive Services and APIM so names/quota free up).
3. Delete the budget and any leftover resource groups; confirm $0 run-rate in Cost Management.
4. The repo must still work afterwards: `azd up` from a fresh clone recreates everything, and the
   CLI works locally against any OpenAI-compatible endpoint.
