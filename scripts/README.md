# Scripts

Three steps, in order. Use the Windows or the Linux version, not both. Run the
commands from the repository root.

## Prerequisites

| Tool | Platform |
|---|---|
| Azure CLI with the `azure-iot` extension | All |
| Git | All |
| PowerShell 7 | Windows |
| `jq` and `envsubst` (package `gettext-base` on Ubuntu) | Linux and Mac |

Create a resource group:

```powershell
az group create --name ai-on-edge-rg --location uksouth
```

## 1. Azure resources

```powershell
.\scripts\windows\deploy_infra.ps1 -ResourceGroup ai-on-edge-rg
```

```bash
./scripts/linux/deploy_infra.sh ai-on-edge-rg
```

The script creates the IoT Hub, the Container Registry and a Log Analytics
workspace for the hub's logs. It registers the Jetson as an edge device. It
creates a service principal that can only pull images.

The script writes a `.env` file at the repository root. The next two steps
read it.

> [!WARNING]
> The `.env` file contains secrets. It is git-ignored. Do not commit it. The
> script overwrites it, so copy any value you added by hand before you run it.

`IOT_DEVICE_CONNECTION_STRING` in `.env` goes into `/etc/aziot/config.toml` on
the Jetson.

The registry password expires after 29 days. Run the script again to get a new
one, then run step 3 again.

## 2. Images

> [!NOTE]
> Commit your changes first. The script tags each image with the last commit
> that changed its module folder. It stops if that folder has changes that are
> not committed.

```powershell
.\scripts\windows\build_modules.ps1
```

```bash
./scripts/linux/build_modules.sh
```

The script builds both images with ACR Tasks and pushes them to the registry.
ACR Tasks build arm64 images under emulation on amd64 hosts. The detector
export needs PyTorch, which fails under emulation, so `camera-processor` builds
through `acr-task.yaml`: the export runs on amd64 first. Each image takes about
10 minutes to build. The script skips an image that the registry already holds
with its tag. Use `-Force` on Windows, or `FORCE=1` on Linux, to build it again.

To build one image:

```powershell
.\scripts\windows\build_modules.ps1 -Module camera-processor
```

```bash
./scripts/linux/build_modules.sh camera-processor
```

## 3. Deployment

```powershell
.\scripts\windows\deploy_modules.ps1
```

```bash
./scripts/linux/deploy_modules.sh
```

The script fills in the manifest from `.env` and applies it to the device. It
finds the tag of each image in the same way as the build script. A module
restarts only when its tag changes, so a camera change does not restart `vllm`.

On the first start, the `vllm` module downloads 4.4 GB of weights. This takes
several minutes. Each start then takes about 6 minutes, with zram set up on
the device. Detection and counting start before `vllm` is ready.

Watch from the device:

```bash
sudo iotedge list
sudo iotedge logs cameraprocessor -f
sudo iotedge logs vllm -f
```

## Preview

On the same network as the device, open `http://<device-ip>:8090/stream`, or run
the viewer. See [modules/viewer/README.md](../modules/viewer/README.md).

## Device audit

`linux/audit_device.sh` lists the host settings that affect memory and the GPU:
swap, power mode, the desktop, Docker and IoT Edge. It changes nothing. Run it
on the device.
