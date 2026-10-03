# The device image. Build it with acr-task.yaml, which first exports the
# detector to ./detector on amd64 (see export.Dockerfile).
#
# The base is the vllm module's base, so the device stores one CUDA stack. The
# base resolves packages from NVIDIA's Jetson index.
FROM ghcr.io/nvidia-ai-iot/vllm@sha256:817f0f940d2d9c9067d861d2118d7bf58c40873598f0c35e19c8516269ebc4bd

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src

# trackers needs PyPI's opencv-python, which installs the same cv2 package as
# the base image's opencv-contrib-python, so remove that first. The module uses
# no CUDA OpenCV call; measured on the device, the webcam and the speed are the
# same. opencv-python loads libGL.
RUN apt-get update && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/* \
    && uv pip uninstall opencv-contrib-python

# Fail the build here, not on the device, if the runtime lacks CUDA.
RUN uv pip install --no-cache . \
    && python -c "import onnxruntime as o; p = o.get_available_providers(); print(o.__version__, p); assert 'CUDAExecutionProvider' in p, p" \
    && python -c "import cv2, trackers; print('cv2', cv2.__version__)"

COPY detector/model.onnx detector/classes.json /opt/detector/

ENV PYTHONUNBUFFERED=1 \
    DETECTOR_DIR=/opt/detector \
    VLLM_BASE_URL=http://vllm:8001 \
    SERVED_MODEL_NAME=qwen3-vl-4b \
    IMAGE_SHM_DIR=/shm \
    MJPEG_PORT=8090

EXPOSE 8090

ENTRYPOINT ["python", "-m", "camera_processor"]
CMD []
