# NVIDIA's vLLM build for Jetson Orin, pinned by digest because the tag rolls.
# It is vLLM 0.19.0 with torch 2.10 and FlashInfer compiled for sm_87, on the
# CUDA 12.6 user space. NVIDIA documents this image for Orin on JetPack 7.2.1 too.
FROM ghcr.io/nvidia-ai-iot/vllm@sha256:817f0f940d2d9c9067d861d2118d7bf58c40873598f0c35e19c8516269ebc4bd

# The checkpoint NVIDIA benchmarks on the Orin Nano 8 GB: W4A16 compressed-tensors,
# vision tower kept at bf16. Pinned to a commit so a re-upload cannot change it.
# The cache is on the edge-models volume, so the 4.4 GB download happens once.
# The base sets all three Hugging Face cache variables to a path inside the
# container, and the older two win over HF_HOME, so all three are set here.
ENV MODEL_ID=cyankiwi/Qwen3-VL-4B-Instruct-AWQ-4bit \
    MODEL_REVISION=b6a6f1cc360d21feee7465f8656b3855d61e767a \
    SERVED_MODEL_NAME=qwen3-vl-4b \
    HF_HOME=/models/huggingface \
    HUGGINGFACE_HUB_CACHE=/models/huggingface \
    TRANSFORMERS_CACHE=/models/huggingface \
    SERVE_PORT=8001 \
    VLLM_MAX_MODEL_LEN=1024 \
    VLLM_KV_CACHE_BYTES=268435456 \
    VLLM_GPU_MEMORY_UTILIZATION=0.40 \
    VLLM_ALLOWED_LOCAL_MEDIA_PATH=/shm

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh && mkdir -p /models /shm

EXPOSE 8001

# The first start downloads the weights, so the start period is long.
HEALTHCHECK --interval=15s --timeout=5s --start-period=1200s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://localhost:{os.environ[\"SERVE_PORT\"]}/health', timeout=4)"

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD []
