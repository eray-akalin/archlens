// Cosmos DB (NoSQL): database `archlens` with containers runs (/run_id), jobs (/key_id) and
// cache (/namespace, per-item TTL). Local (key) auth is off; the workload identity gets the
// built-in Data Contributor role on this database only (SECURITY.md §7).
param location string
param name string
param tags object
param principalId string

@description('Free tier (1000 RU/s shared, 25 GB): one account per subscription. false → serverless.')
param freeTier bool = true

@description('Entra object ID of a developer who runs the live storage contract suite; empty = none. Gets the data role on the *-test containers only.')
param developerPrincipalId string = ''

resource account 'Microsoft.DocumentDB/databaseAccounts@2024-11-15' = {
  // checkov:skip=CKV_AZURE_99: reachable only with Entra (managed identity) — local auth is off.
  // checkov:skip=CKV_AZURE_101: as above; network isolation needs Container Apps VNet
  // integration (tracked for later), and the live contract tests run from a developer machine.
  name: name
  location: location
  tags: tags
  kind: 'GlobalDocumentDB'
  properties: {
    databaseAccountOfferType: 'Standard'
    enableFreeTier: freeTier
    capabilities: freeTier ? [] : [{ name: 'EnableServerless' }]
    consistencyPolicy: { defaultConsistencyLevel: 'Session' }
    locations: [{ locationName: location, failoverPriority: 0, isZoneRedundant: false }]
    disableLocalAuth: true
    disableKeyBasedMetadataWriteAccess: true // CKV_AZURE_132: no management writes with keys
    minimalTlsVersion: 'Tls12'
    publicNetworkAccess: 'Enabled'
  }
}

resource database 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases@2024-11-15' = {
  parent: account
  name: 'archlens'
  properties: {
    resource: { id: 'archlens' }
    // Free tier covers 1000 RU/s shared by the database's containers; serverless has no throughput.
    options: freeTier ? { throughput: 1000 } : {}
  }
}

// `*-test`: the live storage contract suite's copies, so it never touches real runs or jobs.
var containers = [
  { name: 'runs', key: '/run_id' }
  { name: 'jobs', key: '/key_id' }
  { name: 'runs-test', key: '/run_id' }
  { name: 'jobs-test', key: '/key_id' }
]

resource collections 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-11-15' = [
  for c in containers: {
    parent: database
    name: c.name
    properties: { resource: { id: c.name, partitionKey: { paths: [c.key], kind: 'Hash' } } }
  }
]

resource cache 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-11-15' = {
  parent: database
  name: 'cache'
  properties: {
    // defaultTtl -1: TTL on, set per item (the `ttl` field archlens.storage.azure writes)
    resource: { id: 'cache', partitionKey: { paths: ['/namespace'], kind: 'Hash' }, defaultTtl: -1 }
  }
}

resource cacheTest 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-11-15' = {
  parent: database
  name: 'cache-test'
  properties: {
    resource: { id: 'cache-test', partitionKey: { paths: ['/namespace'], kind: 'Hash' }, defaultTtl: -1 }
  }
}

var testContainers = ['runs-test', 'jobs-test', 'cache-test']

resource devDataContributor 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-11-15' = [
  for c in (empty(developerPrincipalId) ? [] : testContainers): {
    parent: account
    name: guid(account.id, developerPrincipalId, c)
    properties: {
      roleDefinitionId: '${account.id}/sqlRoleDefinitions/00000000-0000-0000-0000-000000000002'
      principalId: developerPrincipalId
      scope: '${account.id}/dbs/${database.name}/colls/${c}'
    }
    dependsOn: [collections, cacheTest]
  }
]

resource dataContributor 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-11-15' = {
  parent: account
  name: guid(account.id, principalId, 'data-contributor')
  properties: {
    roleDefinitionId: '${account.id}/sqlRoleDefinitions/00000000-0000-0000-0000-000000000002'
    principalId: principalId
    scope: '${account.id}/dbs/${database.name}'
  }
}

output endpoint string = account.properties.documentEndpoint
