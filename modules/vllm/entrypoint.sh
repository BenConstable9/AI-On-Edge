#!/bin/sh
# Starts vLLM from environment variables, so a manifest change retunes the server
# without a rebuild. vLLM downloads the pinned checkpoint into HF_HOME on first start.
set -eu

# The KV cache is sized in bytes rather than as a share of free memory. On Jetson
# the page cache makes "free" unreliable, and the camera module needs its share
# of the same 8 GB.
#
# The image size in --limit-mm-per-prompt only sets the warmup image. vLLM's
# default is the model's largest, which took 418 s on the CPU at start. The
# camera never sends a crop larger than VLLM_WARMUP_IMAGE_PX.
image_px="${VLLM_WARMUP_IMAGE_PX:-448}"
set -- \
    --revision "${MODEL_REVISION}" \
    --served-model-name "${SERVED_MODEL_NAME}" \
    --host 0.0.0.0 \
    --port "${SERVE_PORT}" \
    --max-model-len "${VLLM_MAX_MODEL_LEN}" \
    --kv-cache-memory-bytes "${VLLM_KV_CACHE_BYTES}" \
    --gpu-memory-utilization "${VLLM_GPU_MEMORY_UTILIZATION}" \
    --max-num-seqs 1 \
    --enforce-eager \
    --allowed-local-media-path "${VLLM_ALLOWED_LOCAL_MEDIA_PATH}" \
    --limit-mm-per-prompt "{\"image\": {\"count\": 1, \"width\": ${image_px}, \"height\": ${image_px}}, \"video\": 0}" \
    --mm-processor-cache-gb 0 \
    --skip-mm-profiling

echo "[entrypoint] vllm serve ${MODEL_ID} $*"
exec vllm serve "${MODEL_ID}" "$@"
