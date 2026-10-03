using 'main.bicep'

// Change this: the registry and IoT hub names come from it, and must be unique in all of Azure.
param baseName = 'ai-on-edge'
param location = 'uksouth'
param iotHubSku = 'S1'
param iotHubCapacity = 1
param acrSku = 'Basic'
param telemetryRetentionDays = 30
