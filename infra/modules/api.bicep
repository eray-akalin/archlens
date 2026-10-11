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

@secure()
param appInsightsConnectionString string

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
      secrets: [
        { name: 'api-keys', value: apiKeys }
        { name: 'appinsights', value: appInsightsConnectionString }
      ]
    }
    template: {
      containers: [
        {
          name: 'api'
          image: image
          resources: { cpu: json('0.5'), memory: '1Gi' }
          env: concat(env, [
            { name: 'ARCHLENS_API_KEYS', secretRef: 'api-keys' }
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
