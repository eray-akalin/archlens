// A user-defined type: valid Bicep that checkov 3.3.20 cannot parse.
type tagMap = {
  owner: string
  env: 'dev' | 'prod'
}

param tags tagMap

resource sa 'Microsoft.Storage/storageAccounts@2023-01-01' = {
  name: 'stgtyped'
  location: 'westeurope'
  tags: tags
  sku: {
    name: 'Standard_LRS'
  }
  kind: 'StorageV2'
  properties: {
    allowBlobPublicAccess: true
  }
}
