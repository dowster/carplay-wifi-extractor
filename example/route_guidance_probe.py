#!/usr/bin/env python3
"""Bluetooth-only iAP2 Route Guidance probe.

This deliberately avoids the Wi-Fi/Bonjour/AirPlay CarPlay path. It reuses the
repository's BlueZ RFCOMM transport, iAP2 link layer, Identification, and MFi
authentication plumbing, then watches the control session for Route Guidance
messages.

The experiment is intentionally conservative: it advertises the Route Guidance
message IDs but does not yet invent undocumented navigation-component fields.
That lets us first establish what iOS does with the minimum Bluetooth-only
identity. A later commit can add component metadata from a verified capture.
"""

import argparse
import asyncio
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from iap2.control_session_message import (  # noqa: E402
    CSM_PARAM_STRUCT,
    CSM_START,
    CSM_STRUCT,
    Uint16,
    register_csm,
    read_csm,
    write_csm,
)
from iap2.control_session_message.authentication import (  # noqa: E402
    AuthenticationCertificate,
    AuthenticationFailed,
    AuthenticationResponse,
    AuthenticationSucceeded,
    RequestAuthenticationCertificate,
    RequestAuthenticationChallengeResponse,
)
from iap2.control_session_message.identification import (  # noqa: E402
    BluetoothTransportComponent,
    IdentificationAccepted,
    IdentificationInformation,
    IdentificationRejected,
    PowerProvidingCapability,
    StartIdentification,
    WirelessCarPlayTransportComponent,
)
from iap2.link_layer import IAP2Connection  # noqa: E402
from iap2.mfi_auth_coprocessor import generate_challenge_response, read_certificate  # noqa: E402
from iap2.transport.bluetooth import BluetoothTransport  # noqa: E402

RG_START = 0x5200
RG_UPDATE = 0x5201
RG_MANEUVER = 0x5202
RG_STOP = 0x5203
RG_LANES = 0x5204
RG_NAMES = {
    RG_START: "StartRouteGuidanceUpdates",
    RG_UPDATE: "RouteGuidanceUpdate",
    RG_MANEUVER: "RouteGuidanceManeuverUpdate",
    RG_STOP: "StopRouteGuidanceUpdates",
    RG_LANES: "LaneGuidanceInformation",
}


def ids_blob(*ids):
    return b"".join(struct.pack(">H", msg_id) for msg_id in ids)


def parse_mac(text):
    try:
        parts = [int(x, 16) for x in text.split(":")]
    except ValueError as exc:
        raise argparse.ArgumentTypeError("invalid Bluetooth MAC") from exc
    if len(parts) != 6 or any(x < 0 or x > 255 for x in parts):
        raise argparse.ArgumentTypeError("MAC must look like AA:BB:CC:DD:EE:FF")
    return bytes(parts)


def tlv_dump(payload):
    """Best-effort top-level iAP2 parameter dump without assuming RG schema."""
    pos = 0
    fields = []
    while pos + 4 <= len(payload):
        length, param_id = CSM_PARAM_STRUCT.unpack(payload[pos:pos + 4])
        if length < 4 or pos + length > len(payload):
            fields.append(f"malformed@{pos}:{payload[pos:].hex()}")
            break
        value = payload[pos + 4:pos + length]
        fields.append(f"p{param_id}={value.hex()}")
        pos += length
    if not fields and payload:
        fields.append(payload.hex())
    return " ".join(fields) if fields else "<empty>"


async def read_raw_csm(reader):
    header = await reader.readexactly(6)
    start, length, msg_id = CSM_STRUCT.unpack(header)
    if start != CSM_START:
        raise RuntimeError(f"unexpected CSM start 0x{start:04x}")
    if length < 6:
        raise RuntimeError(f"invalid CSM length {length}")
    payload = await reader.readexactly(length - 6)
    return msg_id, payload


def experimental_start_guidance(display_ids):
    """Build 0x5200 using an explicitly experimental parameter encoding.

    Do not treat this as protocol documentation. It is isolated here so we can
    replace it after comparing with a known-good capture.
    """
    params = bytearray()
    for display_id in display_ids:
        value = struct.pack(">I", display_id)
        params += CSM_PARAM_STRUCT.pack(len(value) + 4, 0) + value
    return CSM_STRUCT.pack(CSM_START, len(params) + 6, RG_START) + params


async def handle_identification(stream, args):
    while True:
        incoming = await read_csm(stream)
        print(f"[identify] {incoming}")
        if isinstance(incoming, StartIdentification):
            kwargs = dict(
                name=args.name,
                model_identifier="bt-rg-probe",
                manufacturer="dowster-lab",
                serial_number="bt-rg-probe-001",
                fireware_version="0.1",
                hardware_version="0.1",
                messages_sent_by_accessory=ids_blob(RG_START, RG_STOP),
                messages_received_from_accessory=ids_blob(RG_UPDATE, RG_MANEUVER, RG_LANES),
                power_providing_capability=PowerProvidingCapability.NONE,
                maximum_current_drawn_from_device=Uint16(0),
                supported_external_accessory_protocol=[],
                app_match_team_id=None,
                current_language="en",
                supported_language=["en"],
                bluetooth_transport_component=[BluetoothTransportComponent(
                    id=Uint16(0),
                    name="Bluetooth",
                    supports_iap2_connection=True,
                    bluetooth_transport_mac=args.mac,
                )],
            )
            if args.supports_carplay:
                kwargs["wireless_car_play_transport_component"] = WirelessCarPlayTransportComponent(
                    id=Uint16(1),
                    name="Bluetooth route-guidance probe",
                    supports_iap2_connection=True,
                    supports_car_play=True,
                )
            await write_csm(stream, IdentificationInformation(**kwargs))
        elif isinstance(incoming, IdentificationAccepted):
            print("[identify] accepted")
            return
        elif isinstance(incoming, IdentificationRejected):
            raise RuntimeError(f"identification rejected: {incoming}")
        else:
            raise RuntimeError(f"unexpected identification message: {incoming}")


async def handle_auth(stream, loop):
    cert = await loop.run_in_executor(None, read_certificate)
    while True:
        incoming = await read_csm(stream)
        print(f"[auth] {incoming}")
        if isinstance(incoming, RequestAuthenticationCertificate):
            await write_csm(stream, AuthenticationCertificate(certificate=cert))
        elif isinstance(incoming, RequestAuthenticationChallengeResponse):
            response = await loop.run_in_executor(
                None, lambda: generate_challenge_response(incoming.challenge)
            )
            await write_csm(stream, AuthenticationResponse(response=response))
        elif isinstance(incoming, AuthenticationSucceeded):
            print("[auth] succeeded")
            return
        elif isinstance(incoming, AuthenticationFailed):
            raise RuntimeError("MFi authentication failed")
        else:
            raise RuntimeError(f"unexpected authentication message: {incoming}")


async def probe_connection(reader, writer, args, loop):
    print("[probe] RFCOMM connection accepted")
    conn = IAP2Connection(writer, reader, loop, max_outgoing=4)
    conn.start()
    stream = conn.control_session

    await handle_identification(stream, args)
    await handle_auth(stream, loop)

    print("[probe] authenticated iAP2-over-Bluetooth session established")
    print("[probe] start Apple Maps navigation on the phone now")

    if args.start_guidance:
        if not args.display_id:
            print("[probe] --start-guidance requested without --display-id; not sending 0x5200")
        else:
            packet = experimental_start_guidance(args.display_id)
            stream.write(packet)
            await stream.drain()
            print(f"[probe] sent EXPERIMENTAL 0x5200 for display ids {args.display_id}")

    while True:
        msg_id, payload = await read_raw_csm(stream)
        name = RG_NAMES.get(msg_id, f"0x{msg_id:04X}")
        marker = "ROUTE" if msg_id in RG_NAMES else "CSM"
        print(f"[{marker}] {name} len={len(payload)} {tlv_dump(payload)}")


async def async_main(args):
    loop = asyncio.get_running_loop()

    for cls in (
        RequestAuthenticationCertificate,
        RequestAuthenticationChallengeResponse,
        AuthenticationSucceeded,
        AuthenticationFailed,
        StartIdentification,
        IdentificationAccepted,
        IdentificationRejected,
    ):
        register_csm(cls)

    def on_connection(reader, writer):
        asyncio.create_task(probe_connection(reader, writer, args, loop))

    print(f"[probe] adapter={args.adapter} mac={':'.join(f'{b:02X}' for b in args.mac)}")
    print(f"[probe] supports_carplay={args.supports_carplay} bonjour=False")
    print("[probe] waiting for iPhone RFCOMM/iAP2 connection ...")

    transport = BluetoothTransport(
        on_connection,
        loop,
        adapter=args.adapter,
        advertise_bonjour=False,
    )
    try:
        await asyncio.Event().wait()
    finally:
        transport.close()


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adapter", default=os.environ.get("IAP2_ADAPTER", "hci1"))
    parser.add_argument(
        "--mac",
        type=parse_mac,
        default=parse_mac(os.environ.get("IAP2_BT_MAC", "00:00:00:00:00:00")),
        help="Bluetooth adapter MAC advertised in iAP2 Identification (or IAP2_BT_MAC)",
    )
    parser.add_argument("--name", default="Bluetooth Route Guidance Probe")
    parser.add_argument(
        "--supports-carplay",
        action="store_true",
        help="Test B: advertise supports_car_play without enabling Bonjour/AirPlay/video",
    )
    parser.add_argument(
        "--start-guidance",
        action="store_true",
        help="send experimental 0x5200 after authentication",
    )
    parser.add_argument(
        "--display-id",
        type=lambda x: int(x, 0),
        action="append",
        default=[],
        help="experimental Route Guidance display/component id; repeatable",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    if args.mac == b"\x00" * 6:
        raise SystemExit("Set --mac or IAP2_BT_MAC to the Linux adapter's real Bluetooth MAC")
    asyncio.run(async_main(args))
