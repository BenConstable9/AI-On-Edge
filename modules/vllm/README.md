# vLLM server

The device module that serves the vision language model. It is NVIDIA's vLLM
build for Jetson Orin, with a start script.

| Item | Value |
|---|---|
| Base image | `ghcr.io/nvidia-ai-iot/vllm`, vLLM 0.19.0, pinned by digest |
| Model | `cyankiwi/Qwen3-VL-4B-Instruct-AWQ-4bit`, pinned to a commit |
| Served name | `qwen3-vl-4b` |
| Port | `8001`, on the IoT Edge network only |

On the first start, the module downloads the model to the `edge-models`
volume. The next starts read it from the volume.

## Settings

[entrypoint.sh](entrypoint.sh) reads these variables. The
[Dockerfile](Dockerfile) sets the defaults, and the manifest can change them.

| Variable | Default | Meaning |
|---|---|---|
| `VLLM_MAX_MODEL_LEN` | `1024` | The longest prompt and answer together, in tokens |
| `VLLM_KV_CACHE_BYTES` | `268435456` | The KV cache size, 256 MiB. It is set in bytes because free memory on the Jetson is not a reliable figure |
| `VLLM_GPU_MEMORY_UTILIZATION` | `0.40` | The share of the total memory that vLLM can use |
| `VLLM_WARMUP_IMAGE_PX` | `448` | The side of the warm-up image. The camera module never sends a larger crop |
| `VLLM_ALLOWED_LOCAL_MEDIA_PATH` | `/shm` | The folder of image crops that the camera module shares |

The camera module sends each crop as a file path in shared memory, not in the
request body.
