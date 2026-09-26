// Fridge dashboard infrastructure
// Deploy with: az deployment group create -g <rg-name> -f main.bicep -p main.bicepparam

@description('Short name used as a prefix for every resource, e.g. fridgedash')
param prefix string = 'fridgedash'

@description('Region for most resources')
param location string = resourceGroup().location

@description('Region for the Static Web App (SWA only supports a limited set of regions)')
@allowed([
  'centralus'
  'eastus2'
  'westus2'
  'westeurope'
  'eastasia'
])
param staticWebAppLocation string = 'eastus2'

@description('Private "Secret address in iCal format" URL from Google Calendar settings')
@secure()
param googleCalendarIcsUrl string

@description('Long random token the fridge display presents to call the render endpoint')
@secure()
param deviceToken string

var uniqueSuffix = uniqueString(resourceGroup().id)
var storageAccountName = take(toLower('${replace(prefix, '-', '')}${uniqueSuffix}'), 24)
var keyVaultName = take('${prefix}-kv-${uniqueSuffix}', 24)
var planName = '${prefix}-plan-${uniqueSuffix}'
var appInsightsName = '${prefix}-ai-${uniqueSuffix}'
var functionAppName = '${prefix}-func-${uniqueSuffix}'
var mapsAccountName = '${prefix}-maps-${uniqueSuffix}'
var staticWebAppName = '${prefix}-swa-${uniqueSuffix}'

var keyVaultSecretsUserRoleId = '4633458b-17de-408a-b874-0445c86b69e6'
var storageTableDataContributorRoleId = '0a9a7e1f-b9d0-4cc4-a60d-0319b160aaa3'
var azureMapsDataReaderRoleId = '423170ca-a8f6-4b0f-8487-9e4eb8f49bfa'

// ---------- Storage (Functions runtime storage + settings/cache tables) ----------

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageAccountName
  location: location
  kind: 'StorageV2'
  sku: {
    name: 'Standard_LRS'
  }
  properties: {
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
  }
}

resource tableService 'Microsoft.Storage/storageAccounts/tableServices@2023-05-01' = {
  parent: storage
  name: 'default'
}

resource settingsTable 'Microsoft.Storage/storageAccounts/tableServices/tables@2023-05-01' = {
  parent: tableService
  name: 'settings'
}

resource widgetCacheTable 'Microsoft.Storage/storageAccounts/tableServices/tables@2023-05-01' = {
  parent: tableService
  name: 'widgetcache'
}

// ---------- Observability ----------

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: appInsightsName
  location: location
  kind: 'web'
  properties: {
    Application_Type: 'web'
  }
}

// ---------- Function App (Consumption plan) ----------

resource hostingPlan 'Microsoft.Web/serverfarms@2023-12-01' = {
  name: planName
  location: location
  kind: 'linux'
  sku: {
    name: 'B1'
    tier: 'Basic'
  }
  properties: {
    reserved: true
  }
}

resource functionApp 'Microsoft.Web/sites@2023-12-01' = {
  name: functionAppName
  location: location
  kind: 'functionapp,linux'
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    serverFarmId: hostingPlan.id
    httpsOnly: true
    siteConfig: {
      linuxFxVersion: 'PYTHON|3.11'
      appSettings: [
        {
          name: 'AzureWebJobsStorage'
          value: 'DefaultEndpointsProtocol=https;AccountName=${storage.name};AccountKey=${storage.listKeys().keys[0].value};EndpointSuffix=${environment().suffixes.storage}'
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
          name: 'STORAGE_ACCOUNT_NAME'
          value: storage.name
        }
        {
          name: 'AZURE_MAPS_CLIENT_ID'
          value: mapsAccount.properties.uniqueId
        }
        {
          name: 'GOOGLE_CALENDAR_ICS_URL'
          value: '@Microsoft.KeyVault(VaultName=${keyVaultName};SecretName=${icsSecret.name})'
        }
        {
          name: 'DEVICE_TOKEN'
          value: '@Microsoft.KeyVault(VaultName=${keyVaultName};SecretName=${deviceTokenSecret.name})'
        }
      ]
    }
  }
}

// ---------- Key Vault ----------

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: keyVaultName
  location: location
  properties: {
    tenantId: subscription().tenantId
    sku: {
      family: 'A'
      name: 'standard'
    }
    enableRbacAuthorization: true
    enabledForTemplateDeployment: true
  }
}

resource icsSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'google-calendar-ics-url'
  properties: {
    value: googleCalendarIcsUrl
  }
}

resource deviceTokenSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'device-token'
  properties: {
    value: deviceToken
  }
}

// ---------- Azure Maps ----------

resource mapsAccount 'Microsoft.Maps/accounts@2023-06-01' = {
  name: mapsAccountName
  location: 'global'
  kind: 'Gen2'
  sku: {
    name: 'G2'
  }
  properties: {
    disableLocalAuth: true
  }
}

// ---------- Static Web App (Standard tier required to link an existing Function App) ----------

resource staticWebApp 'Microsoft.Web/staticSites@2023-12-01' = {
  name: staticWebAppName
  location: staticWebAppLocation
  sku: {
    name: 'Standard'
    tier: 'Standard'
  }
  properties: {}
}

resource linkedBackend 'Microsoft.Web/staticSites/linkedBackends@2023-12-01' = {
  parent: staticWebApp
  name: 'default'
  properties: {
    backendResourceId: functionApp.id
    region: location
  }
}

// ---------- Role assignments for the Function App's managed identity ----------

resource kvRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVault.id, functionApp.id, keyVaultSecretsUserRoleId)
  scope: keyVault
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', keyVaultSecretsUserRoleId)
    principalId: functionApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource tableRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, functionApp.id, storageTableDataContributorRoleId)
  scope: storage
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', storageTableDataContributorRoleId)
    principalId: functionApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource mapsRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(mapsAccount.id, functionApp.id, azureMapsDataReaderRoleId)
  scope: mapsAccount
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', azureMapsDataReaderRoleId)
    principalId: functionApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

// ---------- Outputs ----------

output functionAppName string = functionApp.name
output functionAppHostname string = functionApp.properties.defaultHostName
output staticWebAppName string = staticWebApp.name
output staticWebAppHostname string = staticWebApp.properties.defaultHostname
output keyVaultName string = keyVault.name
output storageAccountName string = storage.name
output mapsAccountName string = mapsAccount.name
output mapsClientId string = mapsAccount.properties.uniqueId
output resourceGroupName string = resourceGroup().name
