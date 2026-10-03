# Deployment

The IoT Edge manifest. It tells the device which modules to run.

## Modules

| Module | Purpose | Image |
|---|---|---|
| `vllm` | Serves Qwen3-VL-4B on the GPU | `vllm:<tag>` |
| `cameraprocessor` | Reads the camera. Detects, tracks and counts. Asks `vllm` to name and describe objects | `camera-processor:<tag>` |

Both images use the same NVIDIA base image. The device stores its layers once.

The modules do not depend on each other to start. `cameraprocessor` detects
and counts when `vllm` is not ready, and starts the descriptions when `vllm`
answers.

## Detection behaviour

The module has one behaviour for every scene, a street or an office. It reads
no settings from the module twin.

| Step | Behaviour |
|---|---|
| Detect | All 80 COCO classes. Boxes down to a score of 0.2 go to the tracker |
| Clean up | One box for each object. A box is dropped if it overlaps a stronger box by IoU 0.7, or lies 80 % inside a stronger box of the same class |
| Track | ByteTrack, with its published settings, one tracker for each class. A box at `MIN_CONFIDENCE` or more can start an object. Weaker boxes only keep a known object |
| Count | Each object that appears is counted one time, by class. The module also counts the objects in view now |
| Pixelate | Each `person` box, before a frame leaves the module |
| Describe | The VLM gets the best view of each object that is at least 64 pixels on its short side. A person crop is not pixelated: the crop stays on the device |

The VLM answers in JSON with a `label` and a `description`. The description
gives the colour and any brand or company name. The label is `null` if the
detector's label is right, or if the VLM is not certain. A `null` label keeps the
detector's label. Otherwise the label is free text, in lowercase. The prompt
lists the 80 COCO names and asks the VLM to use one if it fits, so the counts do
not split across words such as "bus" and "electric bus". It also asks the VLM to
call any human "person".

If the VLM's label is different from the detector's, the track takes the VLM's
label. The counts move to the new label, the picture marks it with `*`, and the
viewer shows the detector's label beside it. A track that the VLM labels
`person` is pixelated.

The detector can give one object two boxes: a car under "car" and "truck", or a
whole car and its cabin. Each extra box became a new object. The clean-up step
removes them. The same-class rule compares the overlap with the smaller box,
because a part lies inside its object. A person in front of a car is a different
class, so the person stays.

The tracker is ByteTrack from Roboflow `trackers` 2.6.1, with its published
settings. A Kalman filter predicts each object, and the boxes are matched in two
rounds: strong boxes first, then weak ones. A lost object keeps its id for 30
frames at 30 fps, scaled to the real frame rate: about 1 second. The clean-up
step is supervision's NMS.

ByteTrack matches boxes by overlap only, whatever their class. With one tracker
for all classes, a person who walked out past a couch handed their id to the
couch. So each class has its own tracker, as BoxMOT's `per_class` option does,
and ids are unique across them. An id never changes class. The detector also
flickers: a car is a truck for a few frames, in the same box. So a box that
overlaps an object of the last frame by IoU 0.7 or more takes that object's
class first. IoU 0.7 is the clean-up step's rule for one object.

The VLM does not get the first view of an object. Each frame, the module scores
the view and keeps the best so far:

| Order | Signal |
|---|---|
| 1 | The whole object is in view, not cut by the frame edge |
| 2 | Pixels not covered by other boxes, up to the 448 pixel crop |
| 3 | Detector score |

When the VLM is free, it gets the object that has waited longest. A whole view
is ready after 1 second. A part view is ready after 2 seconds, so an object at
the edge has time to come into full view. The viewer shows the frame of the view
that the VLM was given.

The VLM and the detector share the GPU. After each VLM answer, the module waits
as long as the answer took, so the VLM is busy for half the time at most. This
keeps the frame rate at about 10 frames a second or more. The VLM label stays
with the track. If the detector later gives the track a label that the VLM was
not asked about, the module asks the VLM again.

An object that leaves before its turn keeps its best view. It waits in a second
queue, which the VLM serves when no object in view is ready, so the answer comes
later. An object is not described only when it was too small, the VLM was still
loading, the VLM failed on it twice, or more than 100 objects that left were
waiting. The viewer shows the reason.

| Variable | Default | Meaning |
|---|---|---|
| `MIN_CONFIDENCE` | `0.7` | The detector score that starts a new object: ByteTrack's `track_activation_threshold`. Weaker boxes, down to 0.2, only keep a known object |
| `CAMERA_SOURCE` | `auto` | `auto` uses the first video device that gives frames. A number or a device path picks one device. A file path plays a video |
| `PREVIEW_WIDTH` | `960` | The width of the preview and the event frames |

Set these in the `env` section of the manifest.

## Common mistakes

| Mistake | Result |
|---|---|
| No `"Runtime": "nvidia"` in `createOptions` | The container has no GPU. Both modules need it |
| A new image under an old tag | The device keeps the old image. The scripts tag each build with its commit to prevent this |
| A fixed device such as `/dev/video0` in `createOptions` | Linux gives a webcam a new number each time it is plugged in. The container then cannot start. The manifest mounts the host's `/dev` read-only at `/hostdev`, and lets the module open video devices only |

## Volumes

| Volume | Module | Contents |
|---|---|---|
| `edge-models` | `vllm` | The 4.4 GB model download. A module update keeps it |
| `/dev/shm/edgeframes` | both | Image crops for the VLM, sent as file paths, not in the request body |

## Placeholders

`deployment.template.json` holds `$NAME` placeholders. The deploy script fills
them from the `.env` file and from the build tag.

| Name | Source |
|---|---|
| `CONTAINER_REGISTRY_SERVER` | Azure Container Registry |
| `CONTAINER_REGISTRY_USERNAME` | The AcrPull service principal |
| `CONTAINER_REGISTRY_PASSWORD` | The AcrPull service principal |
| `VLLM_TAG` | `git-<commit>`, the last commit that changed `modules/vllm`, not counting its Markdown files |
| `CAMERA_TAG` | `git-<commit>`, the last commit that changed `modules/camera_processor`, not counting its Markdown files |

`deployment.generated.json` holds the filled-in manifest. It contains secrets
and is git-ignored.

## Smoke test

`deployment.smoketest.json` runs Microsoft's public simulated temperature
sensor. It needs no registry credentials and no build. Use it to prove that the
device, the IoT Edge runtime and the hub work before you build the images.
SETUP.md step 2.6 tells you how.
