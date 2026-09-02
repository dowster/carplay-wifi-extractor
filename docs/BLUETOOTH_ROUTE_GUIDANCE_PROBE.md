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

`mise` provides the pinned Python runtime and `uv`; `uv` creates `.venv` and
manages the Python dependencies.

From the repository root:

```bash
mise trust
mise install
mise run setup
mise run doctor
```

Equivalent commands without the mise tasks are:

```bash
mise install
uv sync
uv run python example/route_guidance_probe.py --help
```

The old `example/iap2/requirements.txt` is retained for compatibility with the
original project, but the probe should use the root `pyproject.toml`/`uv`
environment.

### Native Linux packages

The Bluetooth transport uses BlueZ over D-Bus and GLib/PyGObject. Those have
native build/runtime dependencies that should be installed through the OS rather
than hidden in the Python environment.

Fedora-family example:

```bash
sudo dnf install -y \
  bluez bluez-tools \
  gcc pkgconf-pkg-config \
  dbus-devel glib2-devel \
  gobject-introspection-devel cairo-gobject-devel
```

Debian/Ubuntu-family example:

```bash
sudo apt update
sudo apt install -y \
  bluez \
  gcc pkg-config \
  libdbus-1-dev libglib2.0-dev \
  libgirepository-2.0-dev libcairo2-dev
```

Package names vary slightly by distro release. If `uv sync` fails while building
`dbus-python` or `PyGObject`, the missing item is normally one of these native
`-devel`/`-dev` packages.

## Linux Bluetooth prerequisites

Use a Bluetooth adapter that supports Bluetooth Classic / BR-EDR and RFCOMM.
BlueZ must expose the adapter as an `hciX` device.

Check adapters and MAC addresses:

```bash
bluetoothctl list
bluetoothctl show
```

You can also inspect the kernel devices with:

```bash
hciconfig -a
```

if your distribution still ships `hciconfig`.

Capture Bluetooth traffic in another terminal:

```bash
sudo btmon
```

## Run Test A: minimum Bluetooth-only identity

Replace `hci1` and the MAC address with your USB adapter values:

```bash
IAP2_ADAPTER=hci1 \
IAP2_BT_MAC=AA:BB:CC:DD:EE:FF \
mise run probe
```

If your BlueZ/D-Bus policy requires root, use the already-created uv environment
directly rather than running a second dependency installation under sudo:

```bash
sudo --preserve-env=IAP2_ADAPTER,IAP2_BT_MAC,IAP2_EMULATE_MFI \
  .venv/bin/python example/route_guidance_probe.py
```

This profile advertises:

- Bluetooth iAP2 transport
- Route Guidance control-session message IDs `0x5200`-`0x5204` as applicable

It deliberately does **not** advertise the existing
`WirelessCarPlayTransportComponent`. It also disables Bonjour/AirPlay
advertisement.

Pair/connect the iPhone to the Linux adapter and watch the console plus `btmon`.

Expected progression:

1. RFCOMM connection
2. iAP2 link negotiation
3. `StartIdentification`
4. `IdentificationAccepted` or `IdentificationRejected`
5. MFi authentication request
6. Authentication success/failure
7. Control-session messages, if authentication succeeds

## Test B: advertise CarPlay capability, still no display/video

If Test A authenticates but does not expose Route Guidance, try:

```bash
IAP2_ADAPTER=hci1 \
IAP2_BT_MAC=AA:BB:CC:DD:EE:FF \
uv run python example/route_guidance_probe.py --supports-carplay
```

If root is required:

```bash
sudo --preserve-env=IAP2_ADAPTER,IAP2_BT_MAC,IAP2_EMULATE_MFI \
  .venv/bin/python example/route_guidance_probe.py --supports-carplay
```

This adds the repository's existing `WirelessCarPlayTransportComponent` with
`supports_car_play=True`, but Bonjour remains disabled and no AirPlay/video
receiver is started.

The difference between Test A and Test B should help determine whether iOS gates
Route Guidance on the broader CarPlay transport identity.

## MFi authentication caveat

The current repository defaults to an **emulated** MFi certificate/challenge
response. That is useful for software tests but a real iPhone is expected to
reject it.

For real hardware authentication, set:

```bash
export IAP2_EMULATE_MFI=0
```

and connect a compatible/authorized MFi authentication coprocessor through the
existing `example/iap2/mfi_auth_coprocessor.py` implementation.

Until a real iPhone reaches `AuthenticationSucceeded`, lack of Route Guidance is
not evidence that Bluetooth-only guidance is unavailable.

## Experimental 0x5200 sender

The probe can optionally emit a `StartRouteGuidanceUpdates` (`0x5200`) CSM:

```bash
IAP2_ADAPTER=hci1 \
IAP2_BT_MAC=AA:BB:CC:DD:EE:FF \
uv run python example/route_guidance_probe.py \
  --start-guidance \
  --display-id 0x1835
```

**Do not treat the current display-ID parameter encoding as protocol
documentation.** It is intentionally isolated as an experiment and should be
validated against a known-good capture before relying on it.

The safer initial milestone is to reach successful iAP2 identification and MFi
authentication, then record all control-session traffic.

## What the logger recognizes

The probe highlights these message IDs when observed:

- `0x5200` StartRouteGuidanceUpdates
- `0x5201` RouteGuidanceUpdate
- `0x5202` RouteGuidanceManeuverUpdate
- `0x5203` StopRouteGuidanceUpdates
- `0x5204` LaneGuidanceInformation

Unknown messages are also logged with their raw top-level TLVs so captures can be
used to extend the decoder without losing data.

## Recommended first capture

Run Test A with `btmon`, pair the phone, and save:

```bash
sudo btmon -w bt-route-guidance.btsnoop
```

Also redirect the probe output:

```bash
IAP2_ADAPTER=hci1 IAP2_BT_MAC=AA:BB:CC:DD:EE:FF \
  uv run python example/route_guidance_probe.py 2>&1 | tee route-guidance-probe.log
```

If it needs root, substitute `.venv/bin/python` under `sudo` as shown above.

The first useful result is the exact point at which the iPhone accepts or rejects
the accessory. From there we can refine the Identification component list and the
Route Guidance subscription packet using observed traffic instead of guesses.
