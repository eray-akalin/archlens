// User-assigned managed identity shared by the API and the worker (SECURITY.md §7). Roles are
// assigned where the resources are declared (storage.bicep, cosmos.bicep, main.bicep for AI).
param location string
param name string
param tags object

resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: name
  location: location
  tags: tags
}

output id string = identity.id
output principalId string = identity.properties.principalId
output clientId string = identity.properties.clientId
