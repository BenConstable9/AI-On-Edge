#!/usr/bin/env bash
# Creates the Azure resources and registers the Jetson as an IoT Edge device,
# then writes a .env file the build and deploy steps read.
#
# Usage: ./deploy_infra.sh [resource-group] [device-suffix]
# baseName and location come from infra/src/main.bicepparam
set -euo pipefail

RESOURCE_GROUP="${1:-ai-on-edge-rg}"
DEVICE_SUFFIX="${2:-orin}"
AUTH_TYPE="${AUTH_TYPE:-shared_private_key}"

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$PROJECT_ROOT"
echo "Project root: ${PROJECT_ROOT}"

if ! az group show --name "$RESOURCE_GROUP" >/dev/null 2>&1; then
    echo "Resource group '${RESOURCE_GROUP}' not found." >&2
    echo "Create it first: az group create --name ${RESOURCE_GROUP} --location uksouth" >&2
    exit 1
fi

BICEP_PARAM_FILE="${PROJECT_ROOT}/infra/src/main.bicepparam"
echo "Deploying resources from ${BICEP_PARAM_FILE}"

# The hub takes no shared access keys, so whoever runs this gets a data role on it.
if [ "$(az account show --query user.type -o tsv)" = "servicePrincipal" ]; then
    DEPLOYER_TYPE=ServicePrincipal
    DEPLOYER_ID=$(az ad sp show --id "$(az account show --query user.name -o tsv)" --query id -o tsv)
else
    DEPLOYER_TYPE=User
    DEPLOYER_ID=$(az ad signed-in-user show --query id -o tsv)
fi

OUTPUTS=$(az deployment group create \
    --resource-group "$RESOURCE_GROUP" \
    --parameters "$BICEP_PARAM_FILE" \
    --parameters deployerPrincipalId="$DEPLOYER_ID" deployerPrincipalType="$DEPLOYER_TYPE" \
    --query properties.outputs -o json)

read_output() { echo "$OUTPUTS" | jq -r ".$1.value"; }

IOT_HUB_NAME=$(read_output iotHubName)
ACR_NAME=$(read_output acrName)
ACR_LOGIN_SERVER=$(read_output acrLoginServer)

echo
echo "Deployed:"
echo "  IoT Hub:          ${IOT_HUB_NAME}"
echo "  Container Reg:    ${ACR_LOGIN_SERVER}"
echo

BASE_NAME="${IOT_HUB_NAME#iot-}"
DEVICE_ID="${BASE_NAME}-${DEVICE_SUFFIX}"

# A new role assignment can take a few minutes to reach the hub.
echo "Waiting for the IoT Hub data role"
for _ in $(seq 1 40); do
    if az iot hub device-identity list --hub-name "$IOT_HUB_NAME" --auth-type login --top 1 -o none 2>/dev/null; then
        ROLE_READY=1
        break
    fi
    sleep 15
done
if [ -z "${ROLE_READY:-}" ]; then
    echo "The IoT Hub data role did not take effect within 10 minutes" >&2
    exit 1
fi

if az iot hub device-identity show --hub-name "$IOT_HUB_NAME" --device-id "$DEVICE_ID" --auth-type login >/dev/null 2>&1; then
    echo "Device '${DEVICE_ID}' already registered."
else
    echo "Registering edge device '${DEVICE_ID}'"
    az iot hub device-identity create \
        --hub-name "$IOT_HUB_NAME" \
        --device-id "$DEVICE_ID" \
        --edge-enabled true \
        --auth-method "$AUTH_TYPE" \
        --auth-type login
fi

# This is what goes into /etc/aziot/config.toml on the Jetson.
DEVICE_CONN=$(az iot hub device-identity connection-string show \
    --hub-name "$IOT_HUB_NAME" --device-id "$DEVICE_ID" --auth-type login --query connectionString -o tsv)

SUBSCRIPTION_ID=$(az account show --query id -o tsv)
TENANT_ID=$(az account show --query tenantId -o tsv)

# IoT Edge pulls with an Entra service principal holding only AcrPull on this
# registry. The ACR admin account is off: it is registry-wide, it can push and
# delete, and it cannot be scoped. The device has no IMDS endpoint, so a managed
# identity is not an option.
SP_NAME="${BASE_NAME}-acr-pull"
echo "Ensuring service principal '${SP_NAME}'"
ACR_ID=$(az acr show --name "$ACR_NAME" --resource-group "$RESOURCE_GROUP" --query id -o tsv)
APP_ID=$(az ad app list --display-name "$SP_NAME" --query "[0].appId" -o tsv)
if [ -z "$APP_ID" ]; then
    APP_ID=$(az ad app create --display-name "$SP_NAME" --query appId -o tsv)
fi
SP_OBJECT_ID=$(az ad sp list --filter "appId eq '${APP_ID}'" --query "[0].id" -o tsv)
if [ -z "$SP_OBJECT_ID" ]; then
    SP_OBJECT_ID=$(az ad sp create --id "$APP_ID" --query id -o tsv)
fi
HAS_ROLE=$(az role assignment list --assignee "$APP_ID" --scope "$ACR_ID" \
    --query "[?roleDefinitionName=='AcrPull'] | length(@)" -o tsv)
if [ "$HAS_ROLE" = "0" ]; then
    az role assignment create --assignee-object-id "$SP_OBJECT_ID" \
        --assignee-principal-type ServicePrincipal --role AcrPull --scope "$ACR_ID" -o none
fi

# Tenant policy caps password lifetime, commonly at 30 days, so this secret is
# short-lived by design. Re-run this script to roll it before it expires.
# GNU date on Linux, BSD date on a Mac.
CREDENTIAL_END=$(date -u -d '+29 days' +%Y-%m-%d 2>/dev/null || date -u -v+29d +%Y-%m-%d)
CREDENTIAL=$(az ad app credential reset --id "$APP_ID" --display-name iotedge-acrpull \
    --end-date "$CREDENTIAL_END" --only-show-errors -o json)
ACR_USERNAME=$(echo "$CREDENTIAL" | jq -r .appId)
ACR_PASSWORD=$(echo "$CREDENTIAL" | jq -r .password)
echo "  AcrPull service principal ready, secret expires ${CREDENTIAL_END}"

cat > "${PROJECT_ROOT}/.env" <<EOF
RESOURCE_GROUP=${RESOURCE_GROUP}
SUBSCRIPTION_ID=${SUBSCRIPTION_ID}
AZURE_TENANT_ID=${TENANT_ID}

IOT_HUB_NAME=${IOT_HUB_NAME}
IOT_DEVICE_ID=${DEVICE_ID}
# Paste into /etc/aziot/config.toml on the Jetson
IOT_DEVICE_CONNECTION_STRING=${DEVICE_CONN}

ACR_NAME=${ACR_NAME}
CONTAINER_REGISTRY_SERVER=${ACR_LOGIN_SERVER}
CONTAINER_REGISTRY_USERNAME=${ACR_USERNAME}
CONTAINER_REGISTRY_PASSWORD=${ACR_PASSWORD}
EOF

echo
echo ".env written. It contains secrets and is git-ignored — keep it that way."
echo
echo "Next: build and push the module images"
echo "  ./scripts/linux/build_modules.sh"
