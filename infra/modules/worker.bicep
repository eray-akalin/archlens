// Container Apps Job `worker` (ARCHITECTURE.md §6): one execution per queued job, `archlens
// worker --once`, 20-minute timeout, one retry. The KEDA azure-queue scale rule authenticates
// with the workload identity — no storage key or SAS anywhere (AZURE.md §3).
param location string
param name string
param tags object
param environmentId string
param identityId string
param image string
param env array
param storageAccountName string
param queueName string

@secure()
param appInsightsConnectionString string

resource worker 'Microsoft.App/jobs@2025-01-01' = {
  name: name
  location: location
  tags: union(tags, { 'azd-service-name': 'worker' })
  identity: { type: 'UserAssigned', userAssignedIdentities: { '${identityId}': {} } }
  properties: {
    environmentId: environmentId
    workloadProfileName: 'Consumption'
    configuration: {
      triggerType: 'Event'
      replicaTimeout: 1200
      replicaRetryLimit: 1
      secrets: [{ name: 'appinsights', value: appInsightsConnectionString }]
      eventTriggerConfig: {
        parallelism: 1
        replicaCompletionCount: 1
        scale: {
          minExecutions: 0
          maxExecutions: 1 // one assessment at a time (the per-key quota is 1 anyway)
          pollingInterval: 30
          rules: [
            {
              name: 'queue'
              type: 'azure-queue'
              metadata: { accountName: storageAccountName, queueName: queueName, queueLength: '1' }
              identity: identityId
            }
          ]
        }
      }
    }
    template: {
      containers: [
        {
          name: 'worker'
          image: image
          command: ['archlens', 'worker', '--once']
          resources: { cpu: json('1'), memory: '2Gi' }
          env: concat(env, [
            { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', secretRef: 'appinsights' }
            { name: 'OTEL_SERVICE_NAME', value: 'archlens-worker' } // App Insights role name
          ])
        }
      ]
    }
  }
}

output name string = worker.name
