#!/usr/bin/env bash
# Fills the placeholders in deployment.template.json from .env and applies the
# result to the IoT Edge device.
#
# Usage: ./deploy_modules.sh [device-id] [hub-name]
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$PROJECT_ROOT"

if [ ! -f .env ]; then
    echo ".env not found. Run scripts/linux/deploy_infra.sh first." >&2
    exit 1
fi

set -a
# shellcheck disable=SC1091
source .env
set +a

DEVICE_ID="${1:-${IOT_DEVICE_ID:-}}"
HUB_NAME="${2:-${IOT_HUB_NAME:-}}"

if [ -z "$DEVICE_ID" ] || [ -z "$HUB_NAME" ]; then
    echo "IOT_DEVICE_ID and IOT_HUB_NAME must be in .env or passed as arguments" >&2
    exit 1
fi

# The same tags build_modules.sh gives: the last commit that changed each folder.
module_tag() {
    local commit
    commit=$(git log -1 --format=%H -- "$1" ":(exclude)$1/*.md")
    echo "git-${commit:0:10}"
}
VLLM_TAG=$(module_tag modules/vllm)
CAMERA_TAG=$(module_tag modules/camera_processor)
export VLLM_TAG CAMERA_TAG

for pair in "vllm:${VLLM_TAG}" "camera-processor:${CAMERA_TAG}"; do
    if ! az acr repository show --name "$ACR_NAME" --image "$pair" -o none 2>/dev/null; then
        echo "${pair} is not in the registry. Run build_modules.sh first." >&2
        exit 1
    fi
    echo "Image: ${pair}"
done

TEMPLATE="${PROJECT_ROOT}/deployment/deployment.template.json"
GENERATED="${PROJECT_ROOT}/deployment/deployment.generated.json"

echo "Rendering ${TEMPLATE}"

# Only substitute the names the template actually uses, so unrelated shell
# variables cannot leak into the deployment.
envsubst '
$CONTAINER_REGISTRY_SERVER
$CONTAINER_REGISTRY_USERNAME
$CONTAINER_REGISTRY_PASSWORD
$VLLM_TAG
$CAMERA_TAG
' < "$TEMPLATE" > "$GENERATED"

if grep -qE '\$[A-Z_]{3,}' "$GENERATED"; then
    echo "Unresolved placeholders in deployment template:" >&2
    grep -oE '\$[A-Z_]{3,}' "$GENERATED" | sort -u >&2
    exit 1
fi

# createOptions is JSON encoded inside JSON, so a malformed one parses fine as
# part of the manifest and only fails at set-modules. Parse it here instead.
python3 - "$GENERATED" <<'PY'
import json, sys

with open(sys.argv[1], encoding="utf-8") as handle:
    doc = json.load(handle)

agent = doc["modulesContent"]["$edgeAgent"]["properties.desired"]
for group in ("systemModules", "modules"):
    for name, module in agent.get(group, {}).items():
        raw = module.get("settings", {}).get("createOptions", "")
        if not raw:
            continue
        try:
            json.loads(raw)
        except json.JSONDecodeError as exc:
            sys.exit(f"Module '{name}': createOptions is not valid JSON - {exc}")

print("Manifest validated: " + ", ".join(agent.get("modules", {})))
PY

echo "Wrote ${GENERATED}"

echo "Applying deployment to device '${DEVICE_ID}' on hub '${HUB_NAME}'"
az iot edge set-modules \
    --hub-name "$HUB_NAME" \
    --device-id "$DEVICE_ID" \
    --content "$GENERATED" \
    --auth-type login

cat <<'EOF'

Deployment applied.

On first start the vllm module downloads 4.4 GB of weights. This takes several
minutes. Detection and counting start before vLLM is ready.

Watch progress on the device with:
  sudo iotedge list
  sudo iotedge logs cameraprocessor -f
  sudo iotedge logs vllm -f

Preview on the local network: http://<device-ip>:8090/stream
EOF
