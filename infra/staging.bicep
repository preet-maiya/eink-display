// Pool of PR staging Function Apps on the existing B1 plan (no extra compute cost).
// Each open PR claims one app; CI points it at per-PR snapshot tables (TABLE_PREFIX=pr<N>).
// Deploy with: make infra-staging   (needs main.bicep deployed first; takes no secrets)

@description('Same prefix used for main.bicep')
param prefix string = 'fridgedash'

@description('Must match the existing App Service plan region')
param location string

@description('Max PRs that can have a live staging env at once')
@minValue(1)
@maxValue(5)
param poolSize int = 5

var uniqueSuffix = uniqueString(resourceGroup().id)
var storageAccountName = take(toLower('${replace(prefix, '-', '')}${uniqueSuffix}'), 24)
var keyVaultName = take('${prefix}-kv-${uniqueSuffix}', 24)
var planName = '${prefix}-plan-${uniqueSuffix}'
var appInsightsName = '${prefix}-ai-${uniqueSuffix}'
var mapsAccountName = '${prefix}-maps-${uniqueSuffix}'

var keyVaultSecretsUserRoleId = '4633458b-17de-408a-b874-0445c86b69e6'
var azureMapsDataReaderRoleId = '423170ca-a8f6-4b0f-8487-9e4eb8f49bfa'

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: storageAccountName
}

resource hostingPlan 'Microsoft.Web/serverfarms@2023-12-01' existing = {
  name: planName
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' existing = {
  name: appInsightsName
}

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: keyVaultName
}

resource mapsAccount 'Microsoft.Maps/accounts@2023-06-01' existing = {
  name: mapsAccountName
}

resource stagingApps 'Microsoft.Web/sites@2023-12-01' = [for i in range(1, poolSize): {
  name: '${prefix}-stg${i}-${uniqueSuffix}'
  location: location
  kind: 'functionapp,linux'
  // CI claims a slot by setting pr=<number>; empty means free
  tags: {
    role: 'pr-staging'
    pr: ''
  }
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    serverFarmId: hostingPlan.id
    httpsOnly: true
    siteConfig: {
      linuxFxVersion: 'PYTHON|3.11'
      alwaysOn: false
      cors: {
        allowedOrigins: []
      }
      appSettings: [
        {
          name: 'AzureWebJobsStorage'
          value: 'DefaultEndpointsProtocol=https;AccountName=${storage.name};AccountKey=${storage.listKeys().keys[0].value};EndpointSuffix=${environment().suffixes.storage}'
        }
        {
          // Shares AzureWebJobsStorage with prod; explicit ID avoids host-lock/key collisions
          name: 'AzureFunctionsWebJobsHostId'
          value: '${prefix}-stg${i}'
        }
        {
          name: 'FUNCTIONS_EXTENSION_VERSION'
          value: '~4'
        }
        {
          name: 'FUNCTIONS_WORKER_RUNTIME'
          value: 'python'
        }
        {
          name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
          value: appInsights.properties.ConnectionString
        }
        {
          name: 'OTEL_SERVICE_NAME'
          value: '${prefix}-stg${i}'
        }
        {
          name: 'STORAGE_ACCOUNT_NAME'
          value: storage.name
        }
        {
          name: 'AZURE_MAPS_CLIENT_ID'
          value: mapsAccount.properties.uniqueId
        }
        {
          name: 'GOOGLE_CALENDAR_ICS_URL'
          value: '@Microsoft.KeyVault(VaultName=${keyVaultName};SecretName=google-calendar-ics-url)'
        }
        {
          // CI calls /api/refresh once per push instead
          name: 'AzureWebJobs.refresh_data.Disabled'
          value: 'true'
        }
        {
          name: 'SETTINGS_REQUIRE_TOKEN'
          value: '1'
        }
        // TABLE_PREFIX and DEVICE_TOKEN are set by CI when a PR claims the app
      ]
    }
  }
}]

resource kvRoleAssignments 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for i in range(0, poolSize): {
  name: guid(keyVault.id, stagingApps[i].id, keyVaultSecretsUserRoleId)
  scope: keyVault
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', keyVaultSecretsUserRoleId)
    principalId: stagingApps[i].identity.principalId
    principalType: 'ServicePrincipal'
  }
}]

resource mapsRoleAssignments 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for i in range(0, poolSize): {
  name: guid(mapsAccount.id, stagingApps[i].id, azureMapsDataReaderRoleId)
  scope: mapsAccount
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', azureMapsDataReaderRoleId)
    principalId: stagingApps[i].identity.principalId
    principalType: 'ServicePrincipal'
  }
}]

output stagingAppNames array = [for i in range(0, poolSize): stagingApps[i].name]
