@description('Base name for all resources')
param baseName string

@description('The location for all resources')
param location string = resourceGroup().location

@description('Tags applied to every resource')
param tags object = {
  workload: 'ai-on-edge'
}

@description('The SKU name for the IoT Hub')
@allowed([
  'F1'
  'S1'
  'S2'
  'S3'
])
param iotHubSku string = 'S1'

@description('The capacity of the IoT Hub')
param iotHubCapacity int = 1

@description('The SKU name for the Azure Container Registry')
@allowed([
  'Basic'
  'Standard'
  'Premium'
])
param acrSku string = 'Basic'

@description('Days to keep the IoT Hub logs')
@minValue(30)
@maxValue(730)
param telemetryRetentionDays int = 30

@description('Object id of whoever runs the deploy scripts. The hub takes no shared access keys, so this principal gets IoT Hub Data Contributor to register the device and deploy modules')
param deployerPrincipalId string = ''

@description('The type of deployerPrincipalId')
@allowed([
  'User'
  'ServicePrincipal'
])
param deployerPrincipalType string = 'User'

var iotHubDataContributor = '4fc6c259-987e-4a07-842e-c321cc9d413f'

var iotHubName = 'iot-${baseName}'
var acrName = replace('acr${baseName}', '-', '')
var workspaceName = 'log-${baseName}'

// Device identity, module deployment and telemetry.
//
// Several AVM defaults are wrong for this design. Each one fails late, with an
// error that points away from the cause:
//
//   publicNetworkAccess defaults to Disabled. With no private endpoint that
//   leaves the hub unreachable, and it surfaces as a DNS error.
//
//   disableLocalAuth, disableDeviceSAS and disableModuleSAS all default to
//   true. IoT Edge authenticates with a SAS connection string, so the device
//   cannot provision. The device reports the disableLocalAuth flag whichever
//   one is actually set, so fixing only that one looks correct and still fails.
//
// disableLocalAuth stays true: it turns off the hub-wide shared access keys,
// such as iothubowner, which could deploy any container to the device. The
// device and module keys stay on, because IoT Edge cannot use Entra ID. People
// and scripts use Entra ID and the data role below. The cost: the built-in
// Event Hubs endpoint takes keys only, so az iot hub monitor-events does not work.
module iotHub 'br/public:avm/res/devices/iot-hub:0.3.0' = {
  name: 'iotHubDeployment'
  params: {
    name: iotHubName
    location: location
    tags: tags
    skuName: iotHubSku
    skuCapacity: iotHubCapacity
    publicNetworkAccess: 'Enabled'
    disableLocalAuth: true
    disableDeviceSAS: false
    disableModuleSAS: false
    roleAssignments: empty(deployerPrincipalId)
      ? []
      : [
          {
            principalId: deployerPrincipalId
            principalType: deployerPrincipalType
            roleDefinitionIdOrName: iotHubDataContributor
          }
        ]
    // Device connects and disconnects, so a device that drops off shows when and why.
    diagnosticSettings: [
      {
        workspaceResourceId: logAnalytics.outputs.resourceId
        logCategoriesAndGroups: [{ categoryGroup: 'allLogs' }]
        metricCategories: [{ category: 'AllMetrics' }]
      }
    ]
  }
}

// Holds the arm64 module images. ACR Tasks build them under emulation on amd64
// hosts, which is enough because the images compile nothing.
//
// The admin account is off: it is registry-wide, it can push and delete, and it
// cannot be scoped. The device pulls with an Entra service principal that holds
// only AcrPull.
module containerRegistry 'br/public:avm/res/container-registry/registry:0.13.1' = {
  name: 'acrDeployment'
  params: {
    name: acrName
    location: location
    tags: tags
    acrSku: acrSku
    acrAdminUserEnabled: false
  }
}

// The IoT Hub logs: device connections, deployments and errors.
module logAnalytics 'br/public:avm/res/operational-insights/workspace:0.16.1' = {
  name: 'workspaceDeployment'
  params: {
    name: workspaceName
    location: location
    tags: tags
    dataRetention: telemetryRetentionDays
  }
}

@description('The name of the IoT Hub')
output iotHubName string = iotHub.outputs.name

@description('The name of the Azure Container Registry')
output acrName string = containerRegistry.outputs.name

@description('The login server for the Azure Container Registry')
output acrLoginServer string = containerRegistry.outputs.loginServer
