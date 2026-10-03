#!/usr/bin/env bash
# Builds the vllm and camera-processor images for arm64 with ACR Tasks.
#
# ACR Tasks build arm64 images under emulation on amd64 hosts. That is enough:
# the images compile nothing, and the one stage that runs PyTorch runs on amd64.
#
# Each image is tagged with the last commit that changed its own folder. A
# change to one module then leaves the other's tag, and its running container,
# alone: a vLLM restart costs minutes. An image already in the registry is not
# built again unless FORCE=1.
#
# Usage: ./build_modules.sh [vllm|camera-processor|all]
set -euo pipefail

MODULE="${1:-all}"
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

: "${ACR_NAME:?ACR_NAME missing from .env}"

echo "Registry: ${ACR_NAME}"

build_module() {
    local name="$1" context="$2" commit TAG

    # The tag names a commit, so the folder must match it. A README is not in
    # the image, so a change to one must not rebuild or restart the module.
    local paths=("$context" ":(exclude)${context}/*.md")
    if [ -n "$(git status --porcelain -- "${paths[@]}")" ]; then
        echo "${context} has uncommitted changes. Commit them first." >&2
        exit 1
    fi
    commit=$(git log -1 --format=%H -- "${paths[@]}")
    TAG="git-${commit:0:10}"

    if [ "${FORCE:-0}" != "1" ] && az acr repository show --name "$ACR_NAME" --image "${name}:${TAG}" -o none 2>/dev/null; then
        echo "${name}:${TAG} is already in the registry. Skipped."
        return
    fi

    # ACR uploads the working copy, not the commit, and a CRLF line ending in a
    # shell script or Dockerfile breaks the container on the device.
    if grep -rlI $'\r' --include='*.sh' --include=Dockerfile --include='*.Dockerfile' "$context"; then
        echo "CRLF line endings in the files above. Re-check them out with git." >&2
        exit 1
    fi

    echo
    echo "Building ${name} from ${context}"
    # ACR Tasks default to one hour. The first build pulls an 8.5 GB base.
    # camera-processor has a task file: its detector export must run on amd64.
    # az resolves --file against the working directory rather than the build
    # context, so run from inside the context and pass '.' as the context.
    if [ -f "${context}/acr-task.yaml" ]; then
        build=(az acr run --registry "${ACR_NAME}" --platform linux/amd64 --timeout 7200 \
            --set "tag=${TAG}" --file acr-task.yaml .)
    else
        build=(az acr build --registry "${ACR_NAME}" --image "${name}:${TAG}" \
            --platform linux/arm64 --timeout 7200 --file Dockerfile .)
    fi
    if ! (cd "$context" && "${build[@]}"); then
        echo "${name} build failed. The task log above holds the cause." >&2
        exit 1
    fi
    echo "${name} pushed as ${ACR_NAME}.azurecr.io/${name}:${TAG}"
}

case "$MODULE" in
    vllm|camera-processor|all) ;;
    *) echo "Unknown module '${MODULE}'. Use vllm, camera-processor or all." >&2; exit 1 ;;
esac
if [ "$MODULE" = "vllm" ] || [ "$MODULE" = "all" ]; then
    build_module vllm "${PROJECT_ROOT}/modules/vllm"
fi
if [ "$MODULE" = "camera-processor" ] || [ "$MODULE" = "all" ]; then
    build_module camera-processor "${PROJECT_ROOT}/modules/camera_processor"
fi

echo
echo "Build complete. Next: deploy to the device"
echo "  ./scripts/linux/deploy_modules.sh"
