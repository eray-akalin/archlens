// GitHub Actions → Azure OpenAI for the PR-mode workflow (M5.2, AZURE.md §4): a user-assigned
// identity that trusts GitHub's OIDC tokens from this repository's main branch and its pull
// requests, and may only call models (Cognitive Services OpenAI User). No secret anywhere: the
// workflow trades its OIDC token for an Entra token (azure-identity WorkloadIdentityCredential).
param location string
param name string
param tags object

@description('Prefix of the sub claim in this repository\'s GitHub OIDC tokens, as `gh api repos/<owner>/<name>/actions/oidc/customization/sub` reports it (sub_claim_prefix), e.g. repo:owner@123/name@456 — GitHub puts the immutable owner and repository IDs there.')
param subjectPrefix string

param aiAccountName string

resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: name
  location: location
  tags: tags
}

var subjects = [
  { name: 'github-main', subject: '${subjectPrefix}:ref:refs/heads/main' }
  { name: 'github-pull-request', subject: '${subjectPrefix}:pull_request' }
]

@batchSize(1) // one identity's federated credentials can't be written concurrently
resource federated 'Microsoft.ManagedIdentity/userAssignedIdentities/federatedIdentityCredentials@2023-01-31' = [
  for s in subjects: {
    parent: identity
    name: s.name
    properties: {
      issuer: 'https://token.actions.githubusercontent.com'
      subject: s.subject
      audiences: ['api://AzureADTokenExchange']
    }
  }
]

resource ai 'Microsoft.CognitiveServices/accounts@2025-06-01' existing = {
  name: aiAccountName
}

var openAiUser = '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd'

resource role 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(ai.id, identity.id, openAiUser)
  scope: ai
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', openAiUser)
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

output clientId string = identity.properties.clientId
