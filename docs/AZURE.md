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
| Log Analytics + Application Insights | pay-as-you-go, daily cap 0.2 GB, no sampling | ~$0–2 | M4 |
| Storage account | Standard LRS; blob container `artifacts`, queue `assessments`, lifecycle 30 d | < $1 | M4 |
| Cosmos DB (NoSQL) | free tier (1000 RU/s shared DB, 25 GB) — or serverless if free tier is already used in the subscription | $0 | M4 |
| Container Apps environment | Consumption | $0 (free grant) | M4 |
| Container App `api` | 0.5 vCPU / 1 GiB, min replicas 0 | $0 | M4 |
| Container Apps Job `worker` | event-driven (queue), 1 vCPU / 2 GiB, timeout 20 min | $0 within grant | M4 |
| Container registry | GitHub Container Registry (public image) instead of ACR | $0 | M4 |

Region: one region for everything — **Poland Central**. The Azure for Students subscription has a
policy (`sys.regionrestriction`) allowing only polandcentral, austriaeast, switzerlandnorth,
italynorth and belgiumcentral; Azure OpenAI is offered only in the first, third and fourth, and all
M4 services exist in each of those except Static Web Apps (none of the allowed regions), so the
web UI is served by the API container (M4.7). Poland Central is chosen for West-Europe-level pricing (checked
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
  main.bicep                   params: location, alertEmail, imageTag, …
  modules/
    identity.bicep             user-assigned MI + role assignments
    monitoring.bicep           Log Analytics, App Insights (daily cap, no sampling)
    storage.bicep              account, blob container, queue, lifecycle policy
    cosmos.bicep               account (free tier or serverless), db `archlens`, containers: runs (/run_id), jobs (/key_id), cache (/namespace, TTL on)
    aca-env.bicep              Container Apps environment (Consumption)
    api.bicep                  Container App: ingress, min 0 replicas, secrets, env
    worker.bicep               Container Apps Job: event trigger on queue, parallelism 1
  ai.bicep                     phase 1, referenced by main.bicep as an existing resource
  kql/                         saved queries (M4.6); run with scripts/kql.sh <file>
  budget.bicep                 subscription-scope budget + alerts
```

As written for M4.4 (2026-10-11): `azure.yaml` is infrastructure-only — the image comes from
GHCR, and a new version ships with `azd env set ARCHLENS_IMAGE_TAG <sha> && azd provision`.
`infra/main.bicep` (resource-group scope, `rg-archlens-ai`) wires `identity`, `monitoring`,
`storage`, `cosmos`, `aca-env`, `api` and `worker` and grants the workload identity *Cognitive
Services OpenAI User* on the phase-1 account, which the app calls directly
(`ARCHLENS_LLM_AUTH=entra`; no APIM, ADR-009); the web UI ships inside the API image (M4.7). Parameters come from the azd environment (`infra/main.parameters.json`):
`ARCHLENS_IMAGE_TAG`, `ARCHLENS_AI_ACCOUNT`, `ARCHLENS_API_KEYS` (secret),
`ARCHLENS_COSMOS_FREE_TIER`. Storage is ZRS with shared-key access off; Cosmos has local auth
and key-based metadata writes off. checkov passes the whole `infra/` tree (`ai.bicep` lost its
user-defined `type`, which checkov couldn't parse); six findings are skipped inline with reasons:
public endpoints on Storage, Cosmos and the AI account (Entra-only access; network isolation needs
Container Apps VNet integration, tracked for later), no geo-replication, and key auth kept on
the AI account for local development.

Key wiring:
- API: `POST /assessments` writes a `Job` and a `RunState` to Cosmos and a message `{run_id}` to
  the queue. Storage layout (`archlens.storage.azure`): Blob `artifacts/runs/<run_id>/{artifacts,
  checkpoints}/…`; Cosmos `runs`, `jobs` (partitioned by key fingerprint, so quota queries stay in
  one partition) and `cache` (per-item TTL; expiry also checked on read). The planned `findings`
  container is dropped: findings live in the report JSON in Blob. Test-only copies for the live
  storage contract suite: blob container `contract-tests`, queue `assessments-test`, Cosmos
  `runs-test`/`jobs-test`/`cache-test`; the developer in `ARCHLENS_DEVELOPER_PRINCIPAL_ID` gets
  data roles on those only. Run it with `ARCHLENS_LIVE_TESTS=1 ARCHLENS_AZURE_STORAGE_ACCOUNT=…
  ARCHLENS_AZURE_COSMOS_ENDPOINT=… uv run pytest -m live tests/unit/storage` (it empties them).
- Worker job: queue-triggered scale rule (prefer managed identity on the scale rule; if the target
  API version doesn't support it, use a queue-scoped SAS connection string in a job secret and
  note it in ADR form); runs `archlens worker --once`, writes artifacts to Blob, state to Cosmos.
- App → LLM: `ARCHLENS_LLM_BASE_URL` = the AI account's v1 endpoint, `ARCHLENS_LLM_AUTH=entra`;
  the workload identity holds *Cognitive Services OpenAI User*. Limits are the app's own: per-key
  quotas, `ARCHLENS_RUN_BUDGET_USD`, retries with backoff.
- App → Storage/Cosmos: user-assigned MI with data-plane roles only (SECURITY.md §7).
- Telemetry: `APPLICATIONINSIGHTS_CONNECTION_STRING` + `ARCHLENS_OTEL_ENABLED=true`.

No API Management (ADR-009, 2026-10-13). Checked 2026-10-12: in the regions the subscription
policy allows, the ARM SKU list offers Consumption only in switzerlandnorth, and creating it fails
there as well as in polandcentral (`ApiManagementCreationDisabledForLocation`: "non-availability
or low capacity"); BasicV2 (italynorth, switzerlandnorth) is ~$150/month; Developer (~$48/month) is
listed for polandcentral, creation not tried. The gateway that was built for it (`apim.bicep`,
`base.xml`, the subscription key read with `listSecrets` inside the app modules) is in commit
`c8523ed`.

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
  As built (M4.1): `python:3.12-slim-trixie` and `ghcr.io/astral-sh/uv` pinned by digest in `ARG`
  defaults (Dependabot `docker` bumps them); `scripts/fetch_scanners.py` downloads the four Go
  binaries over https and checks each sha256 from `config/tools.yaml` (taken from the releases'
  official checksum files) before writing it; semgrep and checkov get one virtualenv each so their
  dependencies can't clash with ArchLens's. Runtime: uid 10001, `ARCHLENS_DATA_DIR=/data`,
  `git` + `ca-certificates` only, default command `archlens serve` (API), `HEALTHCHECK` on
  `GET /healthz`. The CI `image` job builds it and smoke-tests the CLI, every scanner and the
  health route. **Semgrep registry rules are not in the image**: the Semgrep Rules License allows
  internal use only — no redistribution and no offering them as a service. The CLI fetches them at
  run time (local use is internal use). Decided (2026-10-10, option c): the hosted image sets
  `ARCHLENS_SEMGREP_REGISTRY_RULES=false`, which adds semgrep to `tools.yaml`'s `disabled` list, so
  semgrep never runs there and SEC-03 is `unknown` (`tool_not_run`) — honest rather than a fake
  pass. Rules we write ourselves can bring it back later.
- `deploy.yml` builds and pushes the public image on every push to `main` (approved 2026-10-11;
  docs-only changes skip it) and on demand; `azd deploy` joins it in M4.4. The image ships
  `THIRD_PARTY_NOTICES.md` (licenses and source links of the bundled scanners: hadolint is GPL-3.0,
  the semgrep engine LGPL-2.1). The package came out public (it inherits the public repository's
  visibility; anonymous pulls verified 2026-10-11).

## 5. Cost guardrails

- Default parameters are the free/cheap ones (Cosmos free tier, Container Apps scaling to zero).
- Log Analytics daily cap (0.2 GB). No sampling: the SDK takes the App Insights sampling
  percentage from the service and samples per trace, so 50% dropped whole runs (seen 2026-10-12);
  a run is under 1 MB, so the cap is the guardrail. API and worker report as `archlens-api` and
  `archlens-worker` (`OTEL_SERVICE_NAME`).
- `ARCHLENS_RUN_BUDGET_USD` per run (default $1.00) and `budget_usd` per eval config.
- Container Apps min replicas 0; worker job timeout 20 min, retry limit 1.
- Weekly check: Cost Management → cost by resource. Record spend in the milestone log.

## 6. Teardown (before the credit ends)

1. Record the demo video; export final `eval/results/` and sample reports to the repo.
2. `azd down --purge` (purges soft-deleted Cognitive Services so names/quota free up).
3. Delete the budget and any leftover resource groups; confirm $0 run-rate in Cost Management.
4. The repo must still work afterwards: `azd up` from a fresh clone recreates everything, and the
   CLI works locally against any OpenAI-compatible endpoint.
