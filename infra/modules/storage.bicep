// Storage account: blob container `artifacts` (30-day lifecycle) and queue `assessments`.
// Shared-key access is off: the workload identity is the only way in (SECURITY.md §7).
param location string
param name string
param tags object
param principalId string
param queueName string = 'assessments'

@description('Entra object ID of a developer who runs the live storage contract suite; empty = none. Gets data roles on the test container and queue only.')
param developerPrincipalId string = ''

var blobContributor = 'ba92f5b4-2d11-453d-a403-e96b0029c9fe' // Storage Blob Data Contributor
var queueContributor = '974c5e8b-45b9-4653-ba55-5f855dd0fb88' // Storage Queue Data Contributor

resource account 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  // checkov:skip=CKV_AZURE_35: the public endpoint accepts Entra (managed identity) only — shared
  // keys are off. Denying by default needs Container Apps VNet integration; tracked for later.
  // checkov:skip=CKV_AZURE_206: zone-redundant (ZRS) is enough for reports kept 30 days;
  // geo-replication is not offered without a paired region and isn't worth it here.
  name: name
  location: location
  tags: tags
  kind: 'StorageV2'
  sku: { name: 'Standard_ZRS' }
  properties: {
    accessTier: 'Hot'
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false
    defaultToOAuthAuthentication: true
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
  }
}

resource blobs 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: account
  name: 'default'
}

resource artifacts 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobs
  name: 'artifacts'
  properties: { publicAccess: 'None' }
}

resource queues 'Microsoft.Storage/storageAccounts/queueServices@2023-05-01' = {
  parent: account
  name: 'default'
}

resource queue 'Microsoft.Storage/storageAccounts/queueServices/queues@2023-05-01' = {
  parent: queues
  name: queueName
}

// The live storage contract suite (tests/unit/storage, `-m live`) works here, never on the real
// container and queue: a test message on `assessments` would start the worker.
resource testContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobs
  name: 'contract-tests'
  properties: { publicAccess: 'None' }
}

resource testQueue 'Microsoft.Storage/storageAccounts/queueServices/queues@2023-05-01' = {
  parent: queues
  name: '${queueName}-test'
}

resource devBlobRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(developerPrincipalId)) {
  name: guid(testContainer.id, developerPrincipalId, blobContributor)
  scope: testContainer
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', blobContributor)
    principalId: developerPrincipalId
    principalType: 'User'
  }
}

resource devQueueRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(developerPrincipalId)) {
  name: guid(testQueue.id, developerPrincipalId, queueContributor)
  scope: testQueue
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', queueContributor)
    principalId: developerPrincipalId
    principalType: 'User'
  }
}

resource lifecycle 'Microsoft.Storage/storageAccounts/managementPolicies@2023-05-01' = {
  parent: account
  name: 'default'
  properties: {
    policy: {
      rules: [
        {
          name: 'expire-runs'
          enabled: true
          type: 'Lifecycle'
          definition: {
            filters: { blobTypes: ['blockBlob'], prefixMatch: ['artifacts/runs/'] }
            actions: { baseBlob: { delete: { daysAfterModificationGreaterThan: 30 } } }
          }
        }
      ]
    }
  }
}

resource blobRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(artifacts.id, principalId, blobContributor)
  scope: artifacts
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', blobContributor)
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}

resource queueRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(queue.id, principalId, queueContributor)
  scope: queue
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', queueContributor)
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}

output name string = account.name
output queueName string = queue.name
