# Exports RF-DETR to ONNX. Runs as the first step of acr-task.yaml, on the
# build host's own amd64 platform: PyTorch crashes under arm64 emulation, and
# the ONNX file is the same on every architecture.
FROM python:3.11-slim

COPY --from=ghcr.io/astral-sh/uv:0.9.30 /uv /bin/uv

RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# TQDM_DISABLE: the weight download's progress bar crashes the Windows Azure
# CLI that streams this log.
ENV UV_NO_CACHE=1 \
    UV_LINK_MODE=copy \
    TQDM_DISABLE=1 \
    VIRTUAL_ENV=/opt/export \
    PATH=/opt/export/bin:$PATH

WORKDIR /build
COPY pyproject.toml ./
RUN uv venv "$VIRTUAL_ENV" \
    && uv pip install --group export-torch --index-url https://download.pytorch.org/whl/cpu \
    && uv pip install --group export

COPY scripts/export_detector.py ./
ENTRYPOINT ["python", "/build/export_detector.py"]
