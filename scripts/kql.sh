#!/usr/bin/env bash
# Run one of the queries in infra/kql/ against the provisioned Log Analytics workspace (read-only).
# Usage: scripts/kql.sh infra/kql/cost-per-run.kql [resource-group]
set -euo pipefail
file=${1:?usage: scripts/kql.sh infra/kql/<name>.kql [resource-group]}
group=${2:-$(azd env get-value AZURE_RESOURCE_GROUP)}
workspace=$(az monitor log-analytics workspace show -g "$group" -n archlens-logs --query customerId -o tsv)
az monitor log-analytics query -w "$workspace" --analytics-query "$(cat "$file")" -o table
