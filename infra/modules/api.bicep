// Container App `api` (ARCHITECTURE.md §6): `archlens serve`, external HTTPS ingress, scales to
// zero. API keys and the App Insights connection string are Container Apps secrets.
param location string
param name string
param tags object
param environmentId string
param identityId string
param image string
param env array

@secure()
@description('Comma-separated API keys for the X-API-Key header.')
param apiKeys string

@description('API Management instance in front of Azure OpenAI; empty = the app calls Azure OpenAI directly.')
param apimName string = ''

@secure()
param appInsightsConnectionString string

// The app's APIM subscription (apim.bicep); its key goes straight into a secret, never an output.
resource apimSubscription 'Microsoft.ApiManagement/service/subscriptions@2024-05-01' existing = if (!empty(apimName)) {
  name: '${apimName}/archlens-app'
}

var llmSecret = empty(apimName) ? [] : [{ name: 'llm-key', value: apimSubscription!.listSecrets().primaryKey }]
var llmEnv = empty(apimName) ? [] : [{ name: 'ARCHLENS_LLM_API_KEY', secretRef: 'llm-key' }]

resource api 'Microsoft.App/containerApps@2025-01-01' = {
  name: name
  location: location
  tags: union(tags, { 'azd-service-name': 'api' })
  identity: { type: 'UserAssigned', userAssignedIdentities: { '${identityId}': {} } }
  properties: {
    environmentId: environmentId
    workloadProfileName: 'Consumption'
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: { external: true, targetPort: 8000, transport: 'auto', allowInsecure: false }
      secrets: concat([
        { name: 'api-keys', value: apiKeys }
        { name: 'appinsights', value: appInsightsConnectionString }
      ], llmSecret)
    }
    template: {
      containers: [
        {
          name: 'api'
          image: image
          resources: { cpu: json('0.5'), memory: '1Gi' }
          env: concat(env, llmEnv, [
            { name: 'ARCHLENS_API_KEYS', secretRef: 'api-keys' }
            { name: 'OTEL_SERVICE_NAME', value: 'archlens-api' } // App Insights role name
            { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', secretRef: 'appinsights' }
          ])
          probes: [
            { type: 'Liveness', httpGet: { path: '/healthz', port: 8000 }, periodSeconds: 30 }
            { type: 'Readiness', httpGet: { path: '/healthz', port: 8000 }, periodSeconds: 10 }
          ]
        }
      ]
      scale: { minReplicas: 0, maxReplicas: 1 }
    }
  }
}

output url string = 'https://${api.properties.configuration.ingress.fqdn}'
