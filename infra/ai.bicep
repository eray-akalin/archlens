// Phase 1 (docs/AZURE.md §2): Azure OpenAI (Foundry resource) plus the deployments behind the
// roles in config/models.yaml (skeptic and synth reuse evaluator/verifier deployments).
// Capacity is in thousands of tokens per minute and must fit the subscription quota; quota
// checked 2026-10-06 in polandcentral (Tier 1): gpt-5-mini 1000, gpt4.1-mini 5000,
// text-embedding-3-small 1000. Global Standard is pay-per-token: capacity itself costs nothing.
targetScope = 'resourceGroup'

@description('Must be allowed by the subscription policy sys.regionrestriction (docs/AZURE.md §1).')
param location string = 'polandcentral'

@description('Globally unique account name; also the endpoint subdomain.')
param accountName string = 'archlens-ai-${uniqueString(resourceGroup().id)}'

@description('Entra object ID of a developer calling the models locally with ARCHLENS_LLM_AUTH=entra. Empty = no role assignment.')
param developerPrincipalId string = ''

type modelDeployment = {
  @description('Deployment name, referenced by ARCHLENS_MODEL_<ROLE>.')
  name: string
  model: string
  version: string
  @description('Thousands of tokens per minute.')
  capacity: int
}

param deployments modelDeployment[] = [
  { name: 'gpt-5-mini', model: 'gpt-5-mini', version: '2025-08-07', capacity: 200 }
  { name: 'gpt-4.1-mini', model: 'gpt-4.1-mini', version: '2025-04-14', capacity: 100 }
  { name: 'text-embedding-3-small', model: 'text-embedding-3-small', version: '1', capacity: 100 }
]

var tags = { project: 'archlens' }

resource account 'Microsoft.CognitiveServices/accounts@2025-06-01' = {
  name: accountName
  location: location
  tags: tags
  kind: 'AIServices'
  sku: { name: 'S0' }
  properties: {
    customSubDomainName: accountName
    publicNetworkAccess: 'Enabled'
    // Key auth stays available as a fallback for local development (ARCHLENS_LLM_AUTH=key).
    disableLocalAuth: false
  }
}

// The service rejects concurrent deployment writes on one account.
@batchSize(1)
resource modelDeployments 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = [
  for d in deployments: {
    parent: account
    name: d.name
    sku: { name: 'GlobalStandard', capacity: d.capacity }
    properties: {
      model: { format: 'OpenAI', name: d.model, version: d.version }
      // Stay on the pinned version until it is retired (reproducible runs).
      versionUpgradeOption: 'OnceCurrentVersionExpired'
    }
  }
]

// Cognitive Services OpenAI User: call models only; no keys, no management.
var openAiUserRoleId = '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd'

resource developerRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(developerPrincipalId)) {
  name: guid(account.id, developerPrincipalId, openAiUserRoleId)
  scope: account
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', openAiUserRoleId)
    principalId: developerPrincipalId
    principalType: 'User'
  }
}

output accountName string = account.name
output endpoint string = 'https://${account.name}.openai.azure.com/openai/v1/'
