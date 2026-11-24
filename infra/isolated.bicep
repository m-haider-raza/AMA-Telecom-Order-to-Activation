targetScope = 'resourceGroup'

param location string = resourceGroup().location
param deployApps bool = false
param imageTag string = 'isolated-v1'
param audience string = ''
param principals object = {}
@secure()
param adminToken string = ''
@secure()
param partnerToken string = ''
@secure()
param sessionSecret string = ''

var suffix = uniqueString(resourceGroup().id)
var agents = ['validation', 'decomposition', 'provisioning', 'fallout', 'customer_update', 'supervisor']
var names = ['validation', 'decomposition', 'provisioning', 'fallout', 'customer-update', 'supervisor']
var registryName = 'amaiacr${suffix}'
var boundaryName = 'amai-boundary-${suffix}'

resource boundaryIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'amai-boundary-identity'
  location: location
}
resource identities 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = [for name in names: {
  name: 'amai-${name}-identity'
  location: location
}]
resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: registryName
  location: location
  sku: { name: 'Basic' }
  properties: { adminUserEnabled: false }
}
resource boundaryPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(registry.id, boundaryIdentity.id, 'AcrPull')
  scope: registry
  properties: {
    principalId: boundaryIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7f951dda-4ed3-4680-a7ca-43fe172d538d')
  }
}
resource workerPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for (name, i) in names: {
  name: guid(registry.id, identities[i].id, 'AcrPull')
  scope: registry
  properties: {
    principalId: identities[i].properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7f951dda-4ed3-4680-a7ca-43fe172d538d')
  }
}]
resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: 'amaist${suffix}'
  location: location
  kind: 'StorageV2'
  sku: { name: 'Standard_LRS' }
  properties: {
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false
    publicNetworkAccess: 'Disabled'
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
  }
}
resource blobs 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storage
  name: 'default'
  properties: {
    isVersioningEnabled: true
    deleteRetentionPolicy: { enabled: true, days: 7 }
  }
}
resource container 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobs
  name: 'boundary'
  properties: { publicAccess: 'None' }
}
resource boundaryData 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(container.id, boundaryIdentity.id, 'state-only')
  scope: container
  properties: {
    principalId: boundaryIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'ba92f5b4-2d11-453d-a403-e96b0029c9fe')
  }
}
resource network 'Microsoft.Network/virtualNetworks@2024-05-01' = {
  name: 'amai-network'
  location: location
  properties: {
    addressSpace: { addressPrefixes: ['10.246.0.0/23'] }
    subnets: [
      {
        name: 'apps'
        properties: {
          addressPrefix: '10.246.0.0/24'
          delegations: [{ name: 'apps', properties: { serviceName: 'Microsoft.App/environments' } }]
        }
      }
      {
        name: 'private-endpoints'
        properties: { addressPrefix: '10.246.1.0/27', privateEndpointNetworkPolicies: 'Disabled' }
      }
    ]
  }
}
resource dns 'Microsoft.Network/privateDnsZones@2024-06-01' = {
  name: 'privatelink.blob.${az.environment().suffixes.storage}'
  location: 'global'
}
resource link 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01' = {
  parent: dns
  name: 'amai-storage'
  location: 'global'
  properties: { registrationEnabled: false, virtualNetwork: { id: network.id } }
}
resource endpoint 'Microsoft.Network/privateEndpoints@2024-05-01' = {
  name: 'amai-state-endpoint'
  location: location
  properties: {
    subnet: { id: '${network.id}/subnets/private-endpoints' }
    privateLinkServiceConnections: [{
      name: 'state'
      properties: { privateLinkServiceId: storage.id, groupIds: ['blob'] }
    }]
  }
}
resource endpointDns 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-05-01' = {
  parent: endpoint
  name: 'state'
  properties: { privateDnsZoneConfigs: [{ name: 'blob', properties: { privateDnsZoneId: dns.id } }] }
}
resource logs 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: 'amai-logs'
  location: location
  properties: { sku: { name: 'PerGB2018' }, retentionInDays: 30 }
}
resource insights 'Microsoft.Insights/components@2020-02-02' = {
  name: 'amai-insights'
  location: location
  kind: 'web'
  properties: { Application_Type: 'web', WorkspaceResourceId: logs.id }
}
resource environment 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: 'amai-environment'
  location: location
  properties: {
    vnetConfiguration: { infrastructureSubnetId: '${network.id}/subnets/apps', internal: false }
    workloadProfiles: [{ name: 'Consumption', workloadProfileType: 'Consumption' }]
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logs.properties.customerId
        sharedKey: logs.listKeys().primarySharedKey
      }
    }
  }
}
resource boundary 'Microsoft.App/containerApps@2024-03-01' = if (deployApps) {
  name: boundaryName
  location: location
  identity: { type: 'UserAssigned', userAssignedIdentities: { '${boundaryIdentity.id}': {} } }
  properties: {
    managedEnvironmentId: environment.id
    workloadProfileName: 'Consumption'
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: { external: true, targetPort: 8000, transport: 'auto', allowInsecure: false }
      registries: [{ server: '${registryName}.azurecr.io', identity: boundaryIdentity.id }]
      secrets: [
        { name: 'admin', value: adminToken }
        { name: 'partner', value: partnerToken }
        { name: 'session', value: sessionSecret }
      ]
    }
    template: {
      containers: [{
        name: 'boundary'
        image: '${registryName}.azurecr.io/amai:${imageTag}'
        command: ['uvicorn']
        args: ['app.isolated.api:app', '--host', '0.0.0.0', '--port', '8000', '--workers', '1', '--no-access-log']
        resources: { cpu: json('0.5'), memory: '1Gi' }
        env: [
          { name: 'AZURE_CLIENT_ID', value: boundaryIdentity.properties.clientId }
          { name: 'ISOLATED_BOUNDARY_PRINCIPAL_ID', value: boundaryIdentity.properties.principalId }
          { name: 'ISOLATED_TENANT_ID', value: tenant().tenantId }
          { name: 'ISOLATED_AUDIENCE', value: audience }
          { name: 'ISOLATED_PRINCIPALS', value: string(principals) }
          { name: 'ISOLATED_STORAGE_URL', value: storage.properties.primaryEndpoints.blob }
          { name: 'AMA_MODE', value: 'demo' }
          { name: 'AMA_DELIVERY', value: 'simulated' }
          { name: 'DEMO_ADMIN_TOKEN', secretRef: 'admin' }
          { name: 'DEMO_PARTNER_TOKEN', secretRef: 'partner' }
          { name: 'AMA_SESSION_SECRET', secretRef: 'session' }
          { name: 'AZURE_REGION', value: location }
          { name: 'DEMO_PUBLIC_ORIGIN', value: 'https://${boundaryName}.${environment.properties.defaultDomain}' }
          { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: insights.properties.ConnectionString }
        ]
        probes: [{
          type: 'Readiness'
          httpGet: { path: '/isolated-health', port: 8000 }
          initialDelaySeconds: 10
          periodSeconds: 10
        }]
      }]
      scale: { minReplicas: 1, maxReplicas: 1 }
    }
  }
  dependsOn: [boundaryPull, boundaryData, endpointDns]
}
resource workers 'Microsoft.App/containerApps@2024-03-01' = [for (name, i) in names: if (deployApps) {
  name: name == 'customer-update' ? 'amai-${name}-${take(suffix, 8)}' : 'amai-${name}-${suffix}'
  location: location
  identity: { type: 'UserAssigned', userAssignedIdentities: { '${identities[i].id}': {} } }
  properties: {
    managedEnvironmentId: environment.id
    workloadProfileName: 'Consumption'
    configuration: {
      activeRevisionsMode: 'Single'
      registries: [{ server: '${registryName}.azurecr.io', identity: identities[i].id }]
    }
    template: {
      containers: [{
        name: name
        image: '${registryName}.azurecr.io/amai:${imageTag}'
        command: ['python']
        args: ['-m', 'app.isolated.runtime']
        resources: { cpu: json('0.25'), memory: '0.5Gi' }
        env: [
          { name: 'AZURE_CLIENT_ID', value: identities[i].properties.clientId }
          { name: 'ISOLATED_PRINCIPAL_ID', value: identities[i].properties.principalId }
          { name: 'ISOLATED_AGENT', value: agents[i] }
          { name: 'ISOLATED_AUDIENCE', value: audience }
          { name: 'ISOLATED_BOUNDARY_URL', value: 'https://${boundary!.properties.configuration.ingress.fqdn}' }
          { name: 'ISOLATED_VERIFY_DENIALS', value: '1' }
          { name: 'ISOLATED_TEST_STORAGE_URL', value: storage.properties.primaryEndpoints.blob }
          { name: 'ISOLATED_FORBIDDEN_CLIENT_ID', value: identities[(i + 1) % length(names)].properties.clientId }
          { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: insights.properties.ConnectionString }
        ]
      }]
      scale: { minReplicas: 1, maxReplicas: 1 }
    }
  }
  dependsOn: [workerPull]
}]

output registryName string = registry.name
output boundaryName string = boundaryName
output url string = deployApps ? 'https://${boundary!.properties.configuration.ingress.fqdn}' : ''
output identities array = [for (agent, i) in agents: {
  agent: agent
  name: identities[i].name
  oid: identities[i].properties.principalId
  clientId: identities[i].properties.clientId
  resourceId: identities[i].id
}]
output storageUrl string = storage.properties.primaryEndpoints.blob
output logWorkspace string = logs.name
output insightsName string = insights.name
