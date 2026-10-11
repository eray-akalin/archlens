// Log Analytics + workspace-based Application Insights (AZURE.md §5: daily cap, no sampling).
param location string
param prefix string
param tags object

@description('Log Analytics daily ingestion cap in GB; ingestion stops for the day when reached.')
param dailyQuotaGb string = '0.2'

resource workspace 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: '${prefix}-logs'
  location: location
  tags: tags
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
    workspaceCapping: { dailyQuotaGb: json(dailyQuotaGb) }
  }
}

resource insights 'Microsoft.Insights/components@2020-02-02' = {
  name: '${prefix}-insights'
  location: location
  tags: tags
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: workspace.id
    // No sampling: a run is one trace, and the SDK applies this setting per trace, so 50% lost
    // whole runs. Volume is under 1 MB a day; the workspace's daily cap is the cost guardrail.
    SamplingPercentage: 100
    DisableLocalAuth: false // the OpenTelemetry exporter authenticates with the connection string
  }
}

output workspaceName string = workspace.name
output connectionString string = insights.properties.ConnectionString
