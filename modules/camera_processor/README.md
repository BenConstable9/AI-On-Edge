# Camera processor

The device module that looks at the camera. It finds, follows and counts
objects. It asks the `vllm` module to name and describe them, and it serves a
pixelated preview on the local network.

## Pipeline

| File | Job |
|---|---|
| `capture.py` | Reads the webcam in its own thread. It keeps only the newest frame |
| `detector.py` | Runs RF-DETR Small through ONNX Runtime on the GPU, and drops duplicate boxes with supervision's NMS |
| `tracker.py` | Gives each object an id from frame to frame, with one ByteTrack from Roboflow `trackers` for each class |
| `counting.py` | Counts the objects in view, and each object one time when it appears |
| `best_shot.py` | Keeps the best view of each object, crops it for the VLM, and picks the object the VLM gets next |
| `judge.py` | Sends that view to the VLM, and reads its label and description |
| `vllm_client.py` | Writes the crop to shared memory and calls the `vllm` module |
| `privacy.py` | Pixelates people on the preview, in about 16 blocks along the longer side of each box. The VLM gets the crop without pixelation |
| `annotate.py` | Draws the boxes and labels on the preview, and encodes it as JPEG |
| `events.py` | Keeps the last 100 detection events, each with its frame |
| `mjpeg.py` | Serves the preview and the state on port 8090 |
| `telemetry.py` | Sends the counts to IoT Hub every 10 seconds |
| `pipeline.py` | Joins the parts into the frame loop |
| `config.py` | Reads the settings from the environment, one time at start |

The detection behaviour is in [deployment/README.md](../../deployment/README.md).

## Endpoints

The module serves these on port 8090, on the local network only.

| Path | Contents |
|---|---|
| `/stream` | The annotated preview, as a multipart JPEG stream |
| `/state` | The counts, the timings and the latest VLM answers, as JSON |
| `/events?after=N` | The detection events after number `N`, as JSON |
| `/events/N.jpg` | The preview frame of event `N`, with the object marked |

## Image

[acr-task.yaml](acr-task.yaml) builds the image in two steps:

1. [export.Dockerfile](export.Dockerfile) exports RF-DETR to ONNX on amd64.
   PyTorch does not run under arm64 emulation.
2. [Dockerfile](Dockerfile) builds the arm64 device image on NVIDIA's Jetson
   base. It copies in the ONNX file. It replaces the base image's OpenCV with
   PyPI's `opencv-python`, which `trackers` needs.

The build scripts in [scripts](../../scripts/README.md) run the task.

## Tests

The tests need Python 3.10 or later and pip 25.1 or later. They run on any
computer. They do not need a GPU or a camera. On a Linux computer with no
desktop, install `libgl1` and `libglib2.0-0` first: OpenCV needs them.

```bash
cd modules/camera_processor
pip install --group test
python -m pytest
```

## Known limits

Each class has its own tracker, so an id never moves to an object of another
class. A box that overlaps an object of the last frame by IoU 0.7 or more keeps
that object's class, so a class flicker on one object is still one object.

The tracker matches boxes by position only. An object hidden for more than
about 1 second, such as a dog behind legs, gets a new id when it comes back,
and is counted and described again. When a vehicle passes in front of parked
cars, its box can take a parked car's id. A tracker with an appearance feature,
such as BoT-SORT with re-identification, prevents both. This module does not
have one.
