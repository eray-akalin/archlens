// Container Apps environment, Consumption only (free monthly grant), logs to Log Analytics.
// environmentMode must be explicit: since 2026 an environment created without it can come out as
// `Express`, which supports neither Container Apps jobs (the worker) nor OpenTelemetry.
param location string
param name string
param tags object
param workspaceName string

resource workspace 'Microsoft.OperationalInsights/workspaces@2023-09-01' existing = {
  name: workspaceName
}

resource environment 'Microsoft.App/managedEnvironments@2026-07-01' = {
  name: name
  location: location
  tags: tags
  properties: {
    environmentMode: 'WorkloadProfiles'
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: workspace.properties.customerId
        sharedKey: workspace.listKeys().primarySharedKey
      }
    }
    workloadProfiles: [{ name: 'Consumption', workloadProfileType: 'Consumption' }]
  }
}

output id string = environment.id
