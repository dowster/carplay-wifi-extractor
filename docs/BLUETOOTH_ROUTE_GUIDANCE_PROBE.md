# Bluetooth-only Route Guidance probe

Branch: `bluetooth-route-guidance-probe`

This experiment asks one narrow question: can an iPhone maintain an authenticated
iAP2 session over Bluetooth Classic/RFCOMM and expose Route Guidance metadata
without starting the Wi-Fi/AirPlay/video side of CarPlay?

The probe deliberately disables the repository's CarPlay Bonjour advertisement.
It does not create an AirPlay receiver or H.264 display. Avahi is therefore **not**
required for this probe.

## Environment bootstrap: mise + uv

The branch has a root `mise.toml` and `pyproject.toml`.

`mise` provides Python 3.11 and `uv`; `uv` creates `.venv` and manages Python
dependencies.

From the repository root:

```bash
mise trust
mise install
mise run bootstrap
mise run setup
mise run doctor
```

`mise run bootstrap` reads `/etc/os-release` and dispatches to the Ubuntu/Debian
or Fedora native-package bootstrap task.

The probe pins `PyGObject` below 3.50 because newer releases require GLib 2.80+
and are not suitable for older Ubuntu/Debian hosts such as Ubuntu 22.04 without
upgrading the host GLib stack.

### Ubuntu / Debian native packages

Equivalent to `mise run bootstrap-ubuntu`:

```bash
sudo apt-get update
sudo apt-get install -y \
  bluez \
  build-essential \
  pkg-config \
  libcairo2-dev \
  libdbus-1-dev \
  libglib2.0-dev \
  libgirepository1.0-dev \
  libffi-dev
```

### Fedora native packages

Equivalent to `mise run bootstrap-fedora`:

```bash
sudo dnf install -y \
  gcc gcc-c++ \
  pkgconf-pkg-config \
  cairo-devel \
  glib2-devel \
  gobject-introspection-devel \
  libffi-devel \
  dbus-devel \
  bluez bluez-tools
```

The native packages are required because `dbus-python`, PyGObject, and pycairo
link against system D-Bus, GLib/GObject, GObject Introspection, and Cairo.

## Linux Bluetooth prerequisites

Use a Bluetooth adapter that supports Bluetooth Classic / BR-EDR and RFCOMM.
BlueZ must expose the adapter as an `hciX` device.

Check adapters and MAC addresses:

```bash
bluetoothctl list
bluetoothctl show
```

Capture Bluetooth traffic in another terminal:

```bash
sudo btmon -w bt-route-guidance.btsnoop
```

## Run Test A: minimum Bluetooth-only identity

Replace `hci0` and the MAC address with your adapter values:

```bash
IAP2_ADAPTER=hci0 \
IAP2_BT_MAC=AA:BB:CC:DD:EE:FF \
mise run probe
```

If BlueZ/D-Bus permissions require root, use the already-created uv environment:

```bash
sudo --preserve-env=IAP2_ADAPTER,IAP2_BT_MAC,IAP2_EMULATE_MFI \
  .venv/bin/python example/route_guidance_probe.py
```

This profile advertises Bluetooth iAP2 transport and the Route Guidance
control-session message IDs, while deliberately omitting the existing wireless
CarPlay transport component and all Bonjour/AirPlay/video advertisement.

Expected progression:

1. RFCOMM connection
2. iAP2 link negotiation
3. `StartIdentification`
4. `IdentificationAccepted` or `IdentificationRejected`
5. MFi authentication request
6. Authentication success/failure
7. Control-session messages, if authentication succeeds

### Verified on a real iPhone

On September 2, 2026, an iPhone successfully completed the following sequence
against BlueZ 5.64 on `hci0`:

1. Browsed the Linux SDP database and found `00000000-deca-fade-deca-deafdecacaff`
   on RFCOMM channel 3.
2. Opened the RFCOMM channel and exchanged the iAP2 support marker.
3. Completed iAP2 link synchronization.
4. Sent `RequestAuthenticationCertificate` (`0xAA00`) before Identification.
5. Rejected the repository's emulated certificate with `AuthenticationFailed`
   (`0xAA04`), while iOS displayed “Accessory Not Supported.”

This confirms that Class of Device `0x7a020c`, inquiry/page scanning, and the
registered SDP record are sufficient to reach iAP2 on the tested phone. The
commented custom `hciconfig ... inqdata ...` payload was not applied and is not
required for this milestone. Do not commit `btmon` captures: pairing captures
can contain Bluetooth link keys.

## Test B: advertise CarPlay capability, still no display/video

If Test A authenticates but does not expose Route Guidance:

```bash
IAP2_ADAPTER=hci0 \
IAP2_BT_MAC=AA:BB:CC:DD:EE:FF \
uv run python example/route_guidance_probe.py --supports-carplay
```

This adds the repository's existing `WirelessCarPlayTransportComponent` with
`supports_car_play=True`, but Bonjour remains disabled and no AirPlay/video
receiver is started.

## MFi authentication caveat

The repository currently defaults to an emulated MFi certificate/challenge
response. A real iPhone is expected to reject it. For real hardware
authentication:

```bash
export IAP2_EMULATE_MFI=0
export IAP2_MFI_I2C_BUS=/dev/i2c-11
```

and connect a compatible/authorized MFi authentication coprocessor through
`example/iap2/mfi_auth_coprocessor.py`.

When hardware mode is explicitly enabled, the probe now fails instead of
silently reverting to emulation if the configured I2C device cannot be opened.

Until a real iPhone reaches `AuthenticationSucceeded`, lack of Route Guidance is
not evidence that Bluetooth-only guidance is unavailable.

## Experimental 0x5200 sender

The probe can optionally emit `StartRouteGuidanceUpdates` (`0x5200`):

```bash
IAP2_ADAPTER=hci0 \
IAP2_BT_MAC=AA:BB:CC:DD:EE:FF \
uv run python example/route_guidance_probe.py \
  --start-guidance \
  --display-id 0x1835
```

Do not treat the current display-ID parameter encoding as protocol
documentation. It remains an experiment pending validation against a known-good
capture.

The logger highlights `0x5200` through `0x5204` and dumps unknown control-session
messages with raw top-level TLVs for later decoding.
