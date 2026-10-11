// API Management in front of Azure OpenAI (docs/AZURE.md §3, M4.5): one API for the v1 endpoint,
// a subscription for the app (its key becomes a Container Apps secret), managed-identity auth to
// the backend, rate limit + retry (policies/base.xml), request logs to Application Insights.
// Consumption by default (first million calls a month free); Developer for M5.3 is a parameter.
param location string
param name string
param tags object
param aiEndpoint string // https://<account>.openai.azure.com/openai/v1

@allowed(['Consumption', 'Developer'])
param sku string = 'Consumption'

@description('Calls per minute per subscription before APIM answers 429.')
param rateLimitCalls int = 120

param publisherEmail string
param appInsightsId string

@secure()
param appInsightsConnectionString string

resource apim 'Microsoft.ApiManagement/service@2024-05-01' = {
  // checkov:skip=CKV_AZURE_107: the Consumption tier has no VNet integration; callers need a
  // subscription key, and the backend accepts only APIM's managed identity.
  // checkov:skip=CKV_AZURE_174: the gateway must be public for Container Apps without a VNet
  // (tracked with the other network-isolation items).
  name: name
  location: location
  tags: tags
  sku: { name: sku, capacity: sku == 'Consumption' ? 0 : 1 }
  identity: { type: 'SystemAssigned' }
  properties: {
    publisherEmail: publisherEmail
    publisherName: 'ArchLens'
  }
}

resource api 'Microsoft.ApiManagement/service/apis@2024-05-01' = {
  parent: apim
  name: 'openai'
  properties: {
    displayName: 'Azure OpenAI v1'
    path: 'openai/v1'
    protocols: ['https']
    serviceUrl: aiEndpoint
    subscriptionRequired: true
    subscriptionKeyParameterNames: { header: 'Ocp-Apim-Subscription-Key', query: 'subscription-key' }
  }
}

resource forward 'Microsoft.ApiManagement/service/apis/operations@2024-05-01' = {
  parent: api
  name: 'post-any'
  properties: { displayName: 'POST /*', method: 'POST', urlTemplate: '/*' }
}

resource policy 'Microsoft.ApiManagement/service/apis/policies@2024-05-01' = {
  parent: api
  name: 'policy'
  properties: {
    format: 'rawxml'
    value: replace(loadTextContent('../apim/policies/base.xml'), '__RATE_LIMIT_CALLS__', string(rateLimitCalls))
  }
}

resource subscription 'Microsoft.ApiManagement/service/subscriptions@2024-05-01' = {
  parent: apim
  name: 'archlens-app'
  properties: { displayName: 'ArchLens app', scope: api.id, state: 'active' }
}

resource logger 'Microsoft.ApiManagement/service/loggers@2024-05-01' = {
  parent: apim
  name: 'appinsights'
  properties: {
    loggerType: 'applicationInsights'
    resourceId: appInsightsId
    credentials: { connectionString: appInsightsConnectionString }
  }
}

resource diagnostics 'Microsoft.ApiManagement/service/apis/diagnostics@2024-05-01' = {
  parent: api
  name: 'applicationinsights'
  properties: {
    loggerId: logger.id
    alwaysLog: 'allErrors'
    sampling: { samplingType: 'fixed', percentage: 100 }
    verbosity: 'information'
    httpCorrelationProtocol: 'W3C'
    logClientIp: false
  }
}

output name string = apim.name
output principalId string = apim.identity.principalId
output gatewayUrl string = apim.properties.gatewayUrl
output subscriptionName string = subscription.name // its key is read where it's needed, never output
