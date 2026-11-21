targetScope = 'resourceGroup'

@description('User-managed resources remain in the dedicated demo resource group; VNet-integrated Container Apps also creates a platform-managed infrastructure group.')
param location string = resourceGroup().location
param prefix string = 'ama30'
param deployApp bool = false
param imageTag string = 'demo-v1'
@secure()
param adminToken string
@secure()
param partnerToken string
param enableOpenAI bool = false
@description('Use private Blob connectivity when subscription policy disables public storage access. Creates a separate VNet-integrated environment and app.')
param enablePrivateStorage bool = false
@description('Create an Azure SQL database as the order store and the source of the Analytics report. It accepts Microsoft Entra sign-in only and is reachable only through a private endpoint, so it implies enablePrivateStorage. scripts/Deploy.ps1 prepares it once (python -m app.sqlbootstrap) before the app starts.')
param enableSql bool = false
@description('Microsoft Entra administrator of the SQL server: the account that runs the one-time bootstrap. There is no SQL password. Required when enableSql is true.')
param sqlAdminLogin string = ''
@description('Object ID of the Microsoft Entra administrator. Required when enableSql is true.')
param sqlAdminObjectId string = ''
@allowed(['User', 'Group', 'Application'])
param sqlAdminPrincipalType string = 'User'
param openAiModel string = 'gpt-4.1-mini'
param openAiModelVersion string = '2025-04-14'
param budgetEmail string = ''
param budgetStartDate string = utcNow('yyyy-MM-01')
@description('"live" is the production site: customers register and sign in with their own account and the console is for signed-in staff. "demo" is the synthetic-data demonstration.')
@allowed(['demo', 'live'])
param appMode string = 'demo'
@description('How the service team signs in. "token" is the shared manager access token (the original app). "sso" is Microsoft Entra ID single sign-on: one account per person, role-based access and every change attributed to the person who made it. "sso" needs appMode live, ssoClientId and sessionSecret; scripts/Register-StaffSso.ps1 creates the app registration.')
@allowed(['token', 'sso'])
param staffAuth string = 'token'
@description('Application (client) ID of the Microsoft Entra app registration the service team signs in through. Required when staffAuth is sso.')
param ssoClientId string = ''
@description('Hours a staff sign-in lasts before the person must sign in again (the app accepts 0.25 to 12).')
@minValue(1)
@maxValue(12)
param staffSessionHours int = 4
@secure()
@description('Random value of at least 32 characters that signs customer and staff sessions. Required when staffAuth is sso.')
param sessionSecret string = ''
@description('Create Azure Communication Services e-mail (an Azure-managed sender domain, data location Europe) and use it for customer messages and one-time codes. A live site cannot hand out one-time codes without it.')
param enableEmail bool = false
@description('Most orders the live site stores.')
@minValue(200)
param maxOrders int = 5000

var suffix = uniqueString(resourceGroup().id)
var registryName = '${prefix}acr${suffix}'
var storageName = '${prefix}st${suffix}'
var privateNetwork = enablePrivateStorage || enableSql
var appName = privateNetwork ? '${prefix}-private-${suffix}' : '${prefix}-activation-${suffix}'
var sqlServerName = '${prefix}-sql-${suffix}'
var sqlDatabaseName = '${prefix}-db'

resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: '${prefix}-identity'
  location: location
}
resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: registryName
  location: location
  sku: { name: 'Basic' }
  properties: {
    adminUserEnabled: false
    publicNetworkAccess: 'Enabled'
    policies: { exportPolicy: { status: 'enabled' } }
  }
}
resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageName
  location: location
  sku: { name: 'Standard_LRS' }
  kind: 'StorageV2'
  properties: {
    accessTier: 'Hot'
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
    publicNetworkAccess: privateNetwork ? 'Disabled' : 'Enabled'
  }
}
resource network 'Microsoft.Network/virtualNetworks@2024-05-01' = if (privateNetwork) {
  name: '${prefix}-network'
  location: location
  properties: {
    addressSpace: { addressPrefixes: ['10.245.0.0/24'] }
    subnets: [
      {
        name: 'container-apps'
        properties: {
          addressPrefix: '10.245.0.0/27'
          delegations: [{
            name: 'container-apps'
            properties: { serviceName: 'Microsoft.App/environments' }
          }]
        }
      }
      {
        name: 'private-endpoints'
        properties: {
          addressPrefix: '10.245.0.32/27'
          privateEndpointNetworkPolicies: 'Disabled'
        }
      }
    ]
  }
}
resource storageDns 'Microsoft.Network/privateDnsZones@2024-06-01' = if (privateNetwork) {
  name: 'privatelink.blob.${az.environment().suffixes.storage}'
  location: 'global'
}
resource storageDnsLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01' = if (privateNetwork) {
  parent: storageDns
  name: '${prefix}-network-link'
  location: 'global'
  properties: {
    registrationEnabled: false
    virtualNetwork: { id: network!.id }
  }
}
resource storageEndpoint 'Microsoft.Network/privateEndpoints@2024-05-01' = if (privateNetwork) {
  name: '${prefix}-blob-endpoint'
  location: location
  properties: {
    subnet: { id: '${network!.id}/subnets/private-endpoints' }
    privateLinkServiceConnections: [{
      name: 'blob'
      properties: {
        privateLinkServiceId: storage.id
        groupIds: ['blob']
      }
    }]
  }
}
resource storageEndpointDns 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-05-01' = if (privateNetwork) {
  parent: storageEndpoint
  name: 'blob'
  properties: {
    privateDnsZoneConfigs: [{
      name: 'blob'
      properties: { privateDnsZoneId: storageDns!.id }
    }]
  }
}
resource blobs 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storage
  name: 'default'
  properties: {
    deleteRetentionPolicy: { enabled: true, days: 7 }
    isVersioningEnabled: true
  }
}
resource sqlServer 'Microsoft.Sql/servers@2025-01-01' = if (enableSql) {
  name: sqlServerName
  location: location
  tags: { purpose: '${prefix}-demo' }
  properties: {
    version: '12.0'
    minimalTlsVersion: '1.2'
    publicNetworkAccess: 'Disabled'
    administrators: {
      administratorType: 'ActiveDirectory'
      principalType: sqlAdminPrincipalType
      login: sqlAdminLogin
      sid: sqlAdminObjectId
      tenantId: tenant().tenantId
      azureADOnlyAuthentication: true
    }
  }
}
resource sqlDatabase 'Microsoft.Sql/servers/databases@2025-01-01' = if (enableSql) {
  parent: sqlServer
  name: sqlDatabaseName
  location: location
  sku: { name: 'Standard', tier: 'Standard', capacity: 10 }
  properties: {
    collation: 'SQL_Latin1_General_CP1_CI_AS'
    maxSizeBytes: 2147483648
    requestedBackupStorageRedundancy: 'Local'
    zoneRedundant: false
  }
}
resource sqlDns 'Microsoft.Network/privateDnsZones@2024-06-01' = if (enableSql) {
  name: 'privatelink${az.environment().suffixes.sqlServerHostname}'
  location: 'global'
}
resource sqlDnsLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01' = if (enableSql) {
  parent: sqlDns
  name: '${prefix}-sql-dns-link'
  location: 'global'
  properties: {
    registrationEnabled: false
    virtualNetwork: { id: network!.id }
  }
}
resource sqlEndpoint 'Microsoft.Network/privateEndpoints@2024-05-01' = if (enableSql) {
  name: '${prefix}-sql-endpoint'
  location: location
  properties: {
    subnet: { id: '${network!.id}/subnets/private-endpoints' }
    privateLinkServiceConnections: [{
      name: '${prefix}-sql-connection'
      properties: {
        privateLinkServiceId: sqlServer.id
        groupIds: ['sqlServer']
      }
    }]
  }
}
resource sqlEndpointDns 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-05-01' = if (enableSql) {
  parent: sqlEndpoint
  name: 'default'
  properties: {
    privateDnsZoneConfigs: [{
      name: 'sql'
      properties: { privateDnsZoneId: sqlDns.id }
    }]
  }
}
resource orders 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobs
  name: 'orders'
  properties: { publicAccess: 'None' }
}
resource storageRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, identity.id, 'blob-data-contributor')
  scope: storage
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'ba92f5b4-2d11-453d-a403-e96b0029c9fe')
  }
}
resource acrRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(registry.id, identity.id, 'acr-pull')
  scope: registry
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7f951dda-4ed3-4680-a7ca-43fe172d538d')
  }
}
resource workspace 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: '${prefix}-logs-${suffix}'
  location: location
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
    workspaceCapping: { dailyQuotaGb: json('0.1') }
    publicNetworkAccessForIngestion: 'Enabled'
    publicNetworkAccessForQuery: 'Enabled'
  }
}
resource environment 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: privateNetwork ? '${prefix}-private-environment' : '${prefix}-environment'
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: workspace.properties.customerId
        sharedKey: workspace.listKeys().primarySharedKey
      }
    }
    workloadProfiles: [{ name: 'Consumption', workloadProfileType: 'Consumption' }]
    vnetConfiguration: privateNetwork ? {
      infrastructureSubnetId: '${network!.id}/subnets/container-apps'
      internal: false
    } : null
  }
}
resource openai 'Microsoft.CognitiveServices/accounts@2024-10-01' = if (enableOpenAI) {
  name: '${prefix}-ai-${suffix}'
  location: location
  kind: 'OpenAI'
  sku: { name: 'S0' }
  properties: {
    customSubDomainName: '${prefix}-ai-${suffix}'
    disableLocalAuth: true
    publicNetworkAccess: 'Enabled'
  }
}
resource model 'Microsoft.CognitiveServices/accounts/deployments@2024-10-01' = if (enableOpenAI) {
  parent: openai
  name: 'telecom-advisor'
  sku: { name: 'Standard', capacity: 1 }
  properties: {
    model: { format: 'OpenAI', name: openAiModel, version: openAiModelVersion }
    versionUpgradeOption: 'NoAutoUpgrade'
  }
}
resource aiRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (enableOpenAI) {
  name: guid(openai.id, identity.id, 'openai-user')
  scope: openai
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd')
  }
}
resource emailService 'Microsoft.Communication/emailServices@2023-04-01' = if (enableEmail) {
  name: '${prefix}-email-${suffix}'
  location: 'global'
  properties: { dataLocation: 'Europe' }
}
resource emailDomain 'Microsoft.Communication/emailServices/domains@2023-04-01' = if (enableEmail) {
  parent: emailService
  name: 'AzureManagedDomain'
  location: 'global'
  properties: {
    domainManagement: 'AzureManaged'
    userEngagementTracking: 'Disabled'
  }
}
resource communication 'Microsoft.Communication/communicationServices@2023-04-01' = if (enableEmail) {
  name: '${prefix}-comm-${suffix}'
  location: 'global'
  properties: {
    dataLocation: 'Europe'
    linkedDomains: [emailDomain.id]
  }
}

var origin = 'https://${appName}.${environment.properties.defaultDomain}'
var appSecrets = concat(
  [{ name: 'manager-token', value: adminToken }, { name: 'partner-token', value: partnerToken }],
  empty(sessionSecret) ? [] : [{ name: 'session-secret', value: sessionSecret }],
  enableEmail ? [{ name: 'acs-cs', value: communication!.listKeys().primaryConnectionString }] : []
)
var appEnv = concat(
  [
    { name: 'DEMO_ADMIN_TOKEN', secretRef: 'manager-token' }
    { name: 'DEMO_PARTNER_TOKEN', secretRef: 'partner-token' }
    { name: 'AZURE_CLIENT_ID', value: identity.properties.clientId }
    { name: 'AZURE_STORAGE_ACCOUNT_URL', value: storage.properties.primaryEndpoints.blob }
    { name: 'AZURE_REGION', value: location }
    { name: 'DEMO_PUBLIC_ORIGIN', value: origin }
    { name: 'DEMO_TICK_SECONDS', value: '3' }
    { name: 'AZURE_OPENAI_ENDPOINT', value: enableOpenAI ? openai!.properties.endpoint : '' }
    { name: 'AZURE_OPENAI_DEPLOYMENT', value: enableOpenAI ? 'telecom-advisor' : '' }
    { name: 'AMA_SQL_SERVER', value: enableSql ? sqlServer!.properties.fullyQualifiedDomainName : '' }
    { name: 'AMA_SQL_DATABASE', value: enableSql ? sqlDatabaseName : '' }
    { name: 'AMA_SQL_AUTO_SCHEMA', value: 'false' }
  ],
  appMode == 'live' ? [
    { name: 'AMA_MODE', value: 'live' }
    { name: 'AMA_PUBLIC_ORIGIN', value: origin }
    { name: 'AMA_MAX_ORDERS', value: string(maxOrders) }
  ] : [],
  empty(sessionSecret) ? [] : [{ name: 'AMA_SESSION_SECRET', secretRef: 'session-secret' }],
  enableEmail ? [
    { name: 'ACS_CONNECTION_STRING', secretRef: 'acs-cs' }
    { name: 'ACS_EMAIL_SENDER', value: 'DoNotReply@${emailDomain!.properties.mailFromSenderDomain}' }
    { name: 'AMA_DELIVERY', value: 'live' }
  ] : [],
  staffAuth == 'sso' ? [
    { name: 'AMA_STAFF_AUTH', value: 'sso' }
    { name: 'AMA_SSO_TENANT_ID', value: tenant().tenantId }
    { name: 'AMA_SSO_CLIENT_ID', value: ssoClientId }
    { name: 'AMA_STAFF_SESSION_HOURS', value: string(staffSessionHours) }
  ] : []
)
resource app 'Microsoft.App/containerApps@2024-03-01' = if (deployApp) {
  name: appName
  location: location
  identity: { type: 'UserAssigned', userAssignedIdentities: { '${identity.id}': {} } }
  properties: {
    managedEnvironmentId: environment.id
    workloadProfileName: 'Consumption'
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 8000
        transport: 'http'
        allowInsecure: false
      }
      registries: [{ server: registry.properties.loginServer, identity: identity.id }]
      secrets: appSecrets
    }
    template: {
      containers: [{
        name: 'activation'
        image: '${registry.properties.loginServer}/ama-activation:${imageTag}'
        resources: { cpu: json('0.25'), memory: '0.5Gi' }
        env: appEnv
        probes: [
          {
            type: 'Startup'
            httpGet: { path: '/health', port: 8000, scheme: 'HTTP' }
            initialDelaySeconds: 5
            periodSeconds: 5
            failureThreshold: 60
          }
          {
            type: 'Liveness'
            httpGet: { path: '/health', port: 8000, scheme: 'HTTP' }
            periodSeconds: 30
            failureThreshold: 3
          }
          {
            type: 'Readiness'
            httpGet: { path: '/health', port: 8000, scheme: 'HTTP' }
            periodSeconds: 10
            failureThreshold: 3
          }
        ]
      }]
      scale: { minReplicas: 1, maxReplicas: 1 }
    }
  }
  dependsOn: [storageRole, acrRole, orders, model, aiRole, storageDnsLink, storageEndpointDns, sqlDatabase, sqlDnsLink, sqlEndpointDns]
}
resource budget 'Microsoft.Consumption/budgets@2023-11-01' = if (!empty(budgetEmail)) {
  name: '${prefix}-monthly-demo-budget'
  properties: {
    amount: 100
    category: 'Cost'
    timeGrain: 'Monthly'
    timePeriod: {
      startDate: '${budgetStartDate}T00:00:00Z'
    }
    notifications: {
      Actual80: {
        enabled: true
        operator: 'GreaterThanOrEqualTo'
        threshold: 80
        contactEmails: [budgetEmail]
        thresholdType: 'Actual'
      }
      Forecast100: {
        enabled: true
        operator: 'GreaterThanOrEqualTo'
        threshold: 100
        contactEmails: [budgetEmail]
        thresholdType: 'Forecasted'
      }
    }
  }
}

output registryName string = registry.name
output appName string = appName
output environmentName string = environment.name
output storageAccountName string = storage.name
output managedIdentityName string = identity.name
output managedIdentityClientId string = identity.properties.clientId
output managedIdentityPrincipalId string = identity.properties.principalId
output appOrigin string = origin
output emailSender string = enableEmail ? 'DoNotReply@${emailDomain!.properties.mailFromSenderDomain}' : ''
output sqlServerFqdn string = enableSql ? sqlServer!.properties.fullyQualifiedDomainName : ''
output sqlDatabaseName string = enableSql ? sqlDatabaseName : ''
output demoUrl string = deployApp ? 'https://${app!.properties.configuration.ingress.fqdn}' : ''
output aiMode string = enableOpenAI ? 'Regional Azure OpenAI advisor; guarded rules execution' : 'Explicit deterministic rules demo'
