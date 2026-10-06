---
paths:
  - "infra/**"
  - "azure.yaml"
  - ".github/workflows/**"
  - "Dockerfile"
---

# Infrastructure rules

Full spec: `docs/AZURE.md`.

- Infra is Bicep + azd only. No portal-only changes; if something was clicked in the portal, it
  gets codified before the milestone is closed.
- Ask before running `azd up`, `azd provision`, `azd down`, or any `az ... create|delete`.
- Cost-bearing toggles are parameters with cheap defaults: `apimSku` (default `Consumption`),
  `enableRedis` (default `false`). Never change a default to a paid tier.
- No secrets in Bicep outputs, workflow logs, or images. GitHub → Azure auth is OIDC only.
- Managed identity + RBAC for Storage, Cosmos and Azure OpenAI; no account keys in app config.
- GitHub Actions: pin third-party actions to a full commit SHA, set minimal `permissions:`.
  (ArchLens checks these things in other repos — this repo must pass its own rubric.)
