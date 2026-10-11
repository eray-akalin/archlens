// Phase 2 (docs/AZURE.md §3, M4.4): the hosted ArchLens stack in the existing resource group,
// next to the phase-1 Azure OpenAI account (ai.bicep). Cheap defaults only (AZURE.md §5):
// Consumption Container Apps scaling to zero, Cosmos free tier, a capped Log Analytics workspace.
// LLM traffic: with enableApim, app → API Management (subscription key) → Azure OpenAI (APIM's
// managed identity); without it (the default), app → Azure OpenAI with the app's managed identity.
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

@description('Route LLM traffic through API Management (M4.5). Off by default: the Consumption tier cannot be created in any region the subscription allows (2026-10-12), and Developer costs ~$48/month.')
@allowed(['true', 'false'])
param enableApim string = 'false'

@description('API Management tier: Consumption (first 1M calls a month free) or Developer (M5.3).')
@allowed(['Consumption', 'Developer'])
param apimSku string = 'Consumption'

@description('APIM region. Of the regions the subscription policy allows, only switzerlandnorth offers the Consumption tier (ARM SKU list, 2026-10-12); polandcentral has Developer only.')
param apimLocation string = 'switzerlandnorth'

@description('APIM publisher email (required by the service; notifications only).')
param apimPublisherEmail string = 'archlens@example.com'

@description('LLM calls per minute per APIM subscription before the gateway answers 429.')
param apimRateLimitCalls int = 120

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

var useApim = bool(enableApim)

module apim 'modules/apim.bicep' = if (useApim) {
  name: 'apim'
  params: {
    location: apimLocation
    name: '${prefix}-${suffix}'
    tags: tags
    aiEndpoint: 'https://${ai.name}.openai.azure.com/openai/v1'
    sku: apimSku
    rateLimitCalls: apimRateLimitCalls
    publisherEmail: apimPublisherEmail
    appInsightsId: monitoring.outputs.insightsId
    appInsightsConnectionString: monitoring.outputs.connectionString
  }
}

// Cognitive Services OpenAI User: call models only; no keys, no management. Granted to whoever
// calls Azure OpenAI: APIM's identity with enableApim, the app's identity without it.
var openAiUser = '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd'

resource aiRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!useApim) {
  name: guid(ai.id, '${prefix}-workload', openAiUser)
  scope: ai
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', openAiUser)
    principalId: identity.outputs.principalId
    principalType: 'ServicePrincipal'
  }
}

resource apimAiRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (useApim) {
  name: guid(ai.id, '${prefix}-apim', openAiUser)
  scope: ai
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', openAiUser)
    principalId: useApim ? apim!.outputs.principalId : ''
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
var directLlm = [
  { name: 'ARCHLENS_LLM_BASE_URL', value: 'https://${ai.name}.openai.azure.com/openai/v1/' }
  { name: 'ARCHLENS_LLM_AUTH', value: 'entra' }
]
var apimLlm = useApim ? [
  { name: 'ARCHLENS_LLM_BASE_URL', value: '${apim!.outputs.gatewayUrl}/openai/v1/' }
  { name: 'ARCHLENS_LLM_AUTH', value: 'key' } // the APIM subscription key, in its header:
  { name: 'ARCHLENS_LLM_KEY_HEADER', value: 'Ocp-Apim-Subscription-Key' }
] : []

var appEnv = concat(useApim ? apimLlm : directLlm, [
  { name: 'ARCHLENS_STORAGE', value: 'azure' }
  { name: 'ARCHLENS_AZURE_STORAGE_ACCOUNT', value: storage.outputs.name }
  { name: 'ARCHLENS_AZURE_COSMOS_ENDPOINT', value: cosmos.outputs.endpoint }
  { name: 'ARCHLENS_AZURE_QUEUE_NAME', value: storage.outputs.queueName }
  { name: 'AZURE_CLIENT_ID', value: identity.outputs.clientId } // DefaultAzureCredential → this MI
  { name: 'ARCHLENS_RUN_BUDGET_USD', value: runBudgetUsd }
  { name: 'ARCHLENS_OTEL_ENABLED', value: 'true' }
])

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
    apimName: useApim ? apim!.outputs.name : '' // the app's APIM subscription key is read in the module
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
    apimName: useApim ? apim!.outputs.name : '' // the app's APIM subscription key is read in the module
    appInsightsConnectionString: monitoring.outputs.connectionString
  }
}

output API_URL string = api.outputs.url
output STORAGE_ACCOUNT string = storage.outputs.name
output COSMOS_ENDPOINT string = cosmos.outputs.endpoint
output WORKER_JOB string = worker.outputs.name
output APIM_GATEWAY_URL string = useApim ? apim!.outputs.gatewayUrl : ''
