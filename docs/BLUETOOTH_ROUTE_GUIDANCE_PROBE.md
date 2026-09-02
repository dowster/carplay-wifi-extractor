# Bluetooth-only Route Guidance probe

Branch: `bluetooth-route-guidance-probe`

This experiment asks one narrow question: can an iPhone maintain an authenticated
iAP2 session over Bluetooth Classic/RFCOMM and expose Route Guidance metadata
without starting the Wi-Fi/AirPlay/video side of CarPlay?

The probe deliberately disables the repository's CarPlay Bonjour advertisement.
It does not create an AirPlay receiver or H.264 display.

## Linux prerequisites

Use a Bluetooth adapter that supports Bluetooth Classic / BR-EDR and RFCOMM.
BlueZ must expose the adapter as an `hciX` device.

Useful packages on Debian/Ubuntu include:

```bash
sudo apt install bluez python3-dbus python3-gi python3-pip i2c-tools
```

Install the repository Python requirements as appropriate for your environment:

```bash
python3 -m pip install -r example/iap2/requirements.txt
```

Check the adapter and its MAC address:

```bash
bluetoothctl list
bluetoothctl show
hciconfig -a
```

It is useful to capture the Bluetooth traffic in another terminal:

```bash
sudo btmon
```

## Run Test A: minimum Bluetooth-only identity

Replace `hci1` and the MAC address with your USB adapter values:

```bash
sudo env \
  IAP2_ADAPTER=hci1 \
  IAP2_BT_MAC=AA:BB:CC:DD:EE:FF \
  python3 example/route_guidance_probe.py
```

This profile advertises:

- Bluetooth iAP2 transport
- Route Guidance control-session message IDs 0x5200-0x5204 as applicable

It deliberately does **not** advertise the existing `WirelessCarPlayTransportComponent`.
It also disables Bonjour/AirPlay advertisement.

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
sudo env \
  IAP2_ADAPTER=hci1 \
  IAP2_BT_MAC=AA:BB:CC:DD:EE:FF \
  python3 example/route_guidance_probe.py --supports-carplay
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
IAP2_EMULATE_MFI=0
```

and connect a compatible/authorized MFi authentication coprocessor through the
existing `example/iap2/mfi_auth_coprocessor.py` implementation.

Until a real iPhone reaches `AuthenticationSucceeded`, lack of Route Guidance is
not evidence that Bluetooth-only guidance is unavailable.

## Experimental 0x5200 sender

The probe can optionally emit a `StartRouteGuidanceUpdates` (`0x5200`) CSM:

```bash
sudo env \
  IAP2_ADAPTER=hci1 \
  IAP2_BT_MAC=AA:BB:CC:DD:EE:FF \
  python3 example/route_guidance_probe.py \
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
sudo env IAP2_ADAPTER=hci1 IAP2_BT_MAC=AA:BB:CC:DD:EE:FF \
  python3 example/route_guidance_probe.py 2>&1 | tee route-guidance-probe.log
```

The first useful result is the exact point at which the iPhone accepts or rejects
the accessory. From there we can refine the Identification component list and the
Route Guidance subscription packet using observed traffic instead of guesses.
