// Phase 2 (docs/AZURE.md §3, M4.4): the hosted ArchLens stack in the existing resource group,
// next to the phase-1 Azure OpenAI account (ai.bicep). Cheap defaults only (AZURE.md §5):
// Consumption Container Apps scaling to zero, Cosmos free tier, a capped Log Analytics workspace.
// The app reaches Azure OpenAI directly with its managed identity until APIM arrives (M4.5).
targetScope = 'resourceGroup'

@description('Must be allowed by the subscription policy sys.regionrestriction (docs/AZURE.md §1).')
param location string = 'polandcentral'

@description('Image tag (commit SHA) of ghcr.io/eray-akalin/archlens, published by deploy.yml.')
param imageTag string

@description('Name of the phase-1 Azure OpenAI account (output `accountName` of ai.bicep).')
param aiAccountName string

@secure()
@description('Comma-separated API keys accepted by the API (X-API-Key).')
param apiKeys string

@description('Cosmos free tier (one per subscription); false → serverless. A string: azd fills parameters from environment text.')
@allowed(['true', 'false'])
param cosmosFreeTier string = 'true'

@description('USD budget guard per assessment (ARCHLENS_RUN_BUDGET_USD).')
param runBudgetUsd string = '1.00'

@description('Entra object ID of the developer who runs the live storage contract suite; empty = no roles. Scoped to the test container, queue and *-test Cosmos containers.')
param developerPrincipalId string = ''

var tags = { project: 'archlens', 'azd-env-name': 'archlens' }
var suffix = uniqueString(resourceGroup().id)
var prefix = 'archlens'
var image = 'ghcr.io/eray-akalin/archlens:${imageTag}'

resource ai 'Microsoft.CognitiveServices/accounts@2025-06-01' existing = {
  name: aiAccountName
}

module identity 'modules/identity.bicep' = {
  name: 'identity'
  params: { location: location, name: '${prefix}-workload', tags: tags }
}

module monitoring 'modules/monitoring.bicep' = {
  name: 'monitoring'
  params: { location: location, prefix: prefix, tags: tags }
}

module storage 'modules/storage.bicep' = {
  name: 'storage'
  params: {
    location: location
    name: 'archlens${suffix}'
    tags: tags
    principalId: identity.outputs.principalId
    developerPrincipalId: developerPrincipalId
  }
}

module cosmos 'modules/cosmos.bicep' = {
  name: 'cosmos'
  params: {
    location: location
    name: '${prefix}-${suffix}'
    tags: tags
    principalId: identity.outputs.principalId
    freeTier: bool(cosmosFreeTier)
    developerPrincipalId: developerPrincipalId
  }
}

// Cognitive Services OpenAI User: call models only; no keys, no management.
var openAiUser = '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd'

resource aiRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(ai.id, '${prefix}-workload', openAiUser)
  scope: ai
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', openAiUser)
    principalId: identity.outputs.principalId
    principalType: 'ServicePrincipal'
  }
}

module environment 'modules/aca-env.bicep' = {
  name: 'aca-env'
  params: {
    location: location
    name: '${prefix}-env'
    tags: tags
    workspaceName: monitoring.outputs.workspaceName
  }
}

// Shared by the API and the worker; secrets are added per app.
var appEnv = [
  { name: 'ARCHLENS_STORAGE', value: 'azure' }
  { name: 'ARCHLENS_AZURE_STORAGE_ACCOUNT', value: storage.outputs.name }
  { name: 'ARCHLENS_AZURE_COSMOS_ENDPOINT', value: cosmos.outputs.endpoint }
  { name: 'ARCHLENS_AZURE_QUEUE_NAME', value: storage.outputs.queueName }
  { name: 'AZURE_CLIENT_ID', value: identity.outputs.clientId } // DefaultAzureCredential → this MI
  { name: 'ARCHLENS_LLM_BASE_URL', value: 'https://${ai.name}.openai.azure.com/openai/v1/' }
  { name: 'ARCHLENS_LLM_AUTH', value: 'entra' }
  { name: 'ARCHLENS_RUN_BUDGET_USD', value: runBudgetUsd }
  { name: 'ARCHLENS_OTEL_ENABLED', value: 'true' }
]

module api 'modules/api.bicep' = {
  name: 'api'
  params: {
    location: location
    name: '${prefix}-api'
    tags: tags
    environmentId: environment.outputs.id
    identityId: identity.outputs.id
    image: image
    env: appEnv
    apiKeys: apiKeys
    appInsightsConnectionString: monitoring.outputs.connectionString
  }
}

module worker 'modules/worker.bicep' = {
  name: 'worker'
  params: {
    location: location
    name: '${prefix}-worker'
    tags: tags
    environmentId: environment.outputs.id
    identityId: identity.outputs.id
    image: image
    env: appEnv
    storageAccountName: storage.outputs.name
    queueName: storage.outputs.queueName
    appInsightsConnectionString: monitoring.outputs.connectionString
  }
}

output API_URL string = api.outputs.url
output STORAGE_ACCOUNT string = storage.outputs.name
output COSMOS_ENDPOINT string = cosmos.outputs.endpoint
output WORKER_JOB string = worker.outputs.name
