import argparse
import asyncio
import os
import socket
from typing import Optional

from iap2.client.bluetooth import ClientBluetoothService
from iap2.control_session_message import register_csm, read_csm, write_csm
from iap2.control_session_message.authentication import RequestAuthenticationCertificate, AuthenticationCertificate, \
    RequestAuthenticationChallengeResponse, AuthenticationResponse, AuthenticationSucceeded, AuthenticationFailed
from iap2.control_session_message.identification import StartIdentification, IdentificationInformation, \
    IdentificationAccepted, IdentificationRejected
from iap2.control_session_message.wifi import RequestAccessoryWiFiConfigurationInformation, \
    AccessoryWiFiConfigurationInformation
from iap2.control_session_message.vehicle_status import VehicleStatusUpdate
from iap2.control_session_message.car_play import DeviceTransportIdentifierNotification, WirelessCarPlayUpdate
from iap2.control_session_message.eap import StartExternalAccessoryProtocolSession, StopExternalAccessoryProtocolSession
from iap2.link_layer_client import ClientIAP2Connection, LSPSession

DEFAULT_IAP2_CHALLENGE = bytes.fromhex("000102030405060708090a0b0c0d0e0f10111213")


def _register_default_messages():
    """Ensure all messages used during the handshake are known to the decoder."""
    for msg in (
        RequestAuthenticationCertificate,
        AuthenticationCertificate,
        RequestAuthenticationChallengeResponse,
        AuthenticationResponse,
        AuthenticationSucceeded,
        AuthenticationFailed,
        StartIdentification,
        IdentificationInformation,
        IdentificationAccepted,
        IdentificationRejected,
        DeviceTransportIdentifierNotification,
        WirelessCarPlayUpdate,
        StartExternalAccessoryProtocolSession,
        StopExternalAccessoryProtocolSession,
        VehicleStatusUpdate,
        RequestAccessoryWiFiConfigurationInformation,
        AccessoryWiFiConfigurationInformation,
    ):
        register_csm(msg)


async def _perform_authentication(stream, challenge: Optional[bytes]):
    print("[auth] Requesting accessory certificate…", flush=True)
    await write_csm(stream, RequestAuthenticationCertificate())
    certificate_msg = await read_csm(stream)
    if not isinstance(certificate_msg, AuthenticationCertificate):
        raise RuntimeError(f"Unexpected message while waiting for certificate: {certificate_msg}")
    print(f"[auth] Received certificate ({len(certificate_msg.certificate)} bytes).", flush=True)

    if challenge is None:
        challenge = os.urandom(20)
        print(f"[auth] Generated random {len(challenge)}-byte challenge.", flush=True)
    else:
        print(f"[auth] Using caller-provided {len(challenge)}-byte challenge.", flush=True)

    await write_csm(stream, RequestAuthenticationChallengeResponse(challenge=challenge))
    print("[auth] Waiting for AuthenticationResponse…", flush=True)
    response_msg = await read_csm(stream)
    if not isinstance(response_msg, AuthenticationResponse):
        raise RuntimeError(f"Unexpected message while waiting for auth response: {response_msg}")
    print(f"[auth] Received response ({len(response_msg.response)} bytes).", flush=True)

    # The prototype client does not verify the response. Real clients must validate using Apple's root.
    await write_csm(stream, AuthenticationSucceeded())
    return {
        "certificate": certificate_msg.certificate,
        "challenge": challenge,
        "response": response_msg.response,
    }


async def _perform_identification(stream):
    print("[ident] Sending StartIdentification.", flush=True)
    await write_csm(stream, StartIdentification())
    print("[ident] Waiting for IdentificationInformation…", flush=True)
    try:
        incoming = await read_csm(stream)
    except Exception as exc:
        print(f"[ident] Failed to read IdentificationInformation: {exc}", flush=True)
        raise

    if not isinstance(incoming, IdentificationInformation):
        raise RuntimeError(f"Unexpected identification payload: {incoming}")
    print(f"[ident] Received IdentificationInformation for accessory '{incoming.name}'.", flush=True)
    try:
        await write_csm(stream, IdentificationAccepted())
    except Exception as exc:
        print(f"[ident] Failed to send IdentificationAccepted: {exc}", flush=True)
        raise
    return incoming


async def _request_wifi(stream):
    print("[wifi] Requesting AccessoryWiFiConfigurationInformation.", flush=True)
    await write_csm(stream, RequestAccessoryWiFiConfigurationInformation())
    while True:
        print("[wifi] Waiting for next CSM…", flush=True)
        incoming = await read_csm(stream)
        if isinstance(incoming, AccessoryWiFiConfigurationInformation):
            print("[wifi] Received Wi-Fi configuration payload.", flush=True)
            return incoming
        # Pass through benign notifications while we wait for Wi-Fi info.
        if isinstance(incoming, (WirelessCarPlayUpdate, DeviceTransportIdentifierNotification,
                                 VehicleStatusUpdate)):
            print(f"[wifi] Ignoring interim notification {incoming.__class__.__name__}.", flush=True)
            continue
        raise RuntimeError(f"Unhandled message while waiting for Wi-Fi config: {incoming}")


async def _open_rfcomm(mac: str, channel: int):
    loop = asyncio.get_running_loop()
    print(f"[rfcomm] Connecting to {mac} on RFCOMM channel {channel}…", flush=True)
    sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
    sock.setblocking(False)
    await loop.sock_connect(sock, (mac, channel))
    print("[rfcomm] Channel established.", flush=True)
    reader, writer = await asyncio.open_connection(sock=sock)
    return reader, writer


async def run_client(mac: str, channel: int, challenge: Optional[bytes], register_profile: bool,
                     adapter: str):
    _register_default_messages()
    if register_profile:
        print(f"[profile] Registering client UUID on {adapter} (channel {channel}).", flush=True)
        service = ClientBluetoothService(adapter=adapter, channel=channel)
    else:
        print("[profile] Skipping profile registration (--no-profile).", flush=True)
        service = None
    reader = writer = conn = None
    try:
        while True:
            try:
                reader, writer = await _open_rfcomm(mac, channel)
                break
            except (ConnectionRefusedError, OSError) as exc:
                print(f"[rfcomm] Connect failed ({exc}); retrying in 3s…", flush=True)
                await asyncio.sleep(3)

        loop = asyncio.get_running_loop()
        conn = ClientIAP2Connection(
            writer,
            reader,
            loop,
            max_outgoing=0x7F,
            max_outgoing_delta=0,
            ack_timeout=1000,
        )
        conn.start()
        print("[iap2] Link-layer handshake started, waiting for marker.", flush=True)

        try:
            stream = conn.control_session
            accessory = await _perform_identification(stream)
            auth = await _perform_authentication(stream, challenge)
            wifi = await _request_wifi(stream)
            print("[iap2] Client is running; press Ctrl+C to stop.", flush=True)
            challenge_bytes = auth["challenge"]
            print(f"Challenge ({len(challenge_bytes)} bytes): {challenge_bytes.hex()}")
            print(f"Certificate ({len(auth['certificate'])} bytes) captured.")
            print(f"Authentication response ({len(auth['response'])} bytes) captured.")
            _print_summary(accessory, wifi)

            try:
                while True:
                    await asyncio.sleep(1)
            except asyncio.CancelledError:
                print("[iap2] Client loop cancelled, shutting down…", flush=True)
                raise
        finally:
            print("[iap2] Closing connection.", flush=True)
            if conn:
                try:
                    await asyncio.shield(conn.close())
                except Exception:
                    pass
            if writer:
                writer.close()
                try:
                    await writer.wait_closed()
                except (AttributeError, ConnectionResetError, asyncio.CancelledError):
                    pass
    finally:
        if service:
            print("[profile] Unregistering client UUID.", flush=True)
            service.close()

    return auth, accessory, wifi


def _parse_args():
    parser = argparse.ArgumentParser(description="iAP2 client emulator that requests CarPlay Wi-Fi credentials.")
    parser.add_argument("--mac", required=True, help="Bluetooth MAC address of the CarPlay accessory/server.")
    parser.add_argument("--channel", type=int, default=4, help="RFCOMM channel (default: 3).")
    parser.add_argument("--adapter", default="hci0", help="Bluetooth adapter to use (default: hci0).")
    parser.add_argument("--no-profile", action="store_true",
                        help="Skip registering the CarPlay client profile with BlueZ.")
    parser.add_argument(
        "--challenge",
        help="Optional hex-encoded authentication challenge. Random bytes are used when omitted.",
    )
    return parser.parse_args()


def _decode_hex(value: Optional[str]) -> Optional[bytes]:
    if value is None:
        return None
    try:
        return bytes.fromhex(value)
    except ValueError as exc:
        raise SystemExit(f"Invalid challenge hex string: {exc}") from exc


def _print_summary(accessory: IdentificationInformation, wifi: AccessoryWiFiConfigurationInformation):
    print("Accessory accepted:")
    print(f"  Name: {accessory.name}")
    print(f"  Model: {accessory.model_identifier} ({accessory.hardware_version})")
    print(f"  Manufacturer: {accessory.manufacturer}")
    print("Wi-Fi configuration:")
    print(f"  SSID: {wifi.ssid or '<not provided>'}")
    print(f"  Passphrase: {wifi.passphrase or '<not provided>'}")
    print(f"  Security: {wifi.security_type.name}")
    print(f"  Channel: {int(wifi.channel) if wifi.channel is not None else 'auto'}")


def main():
    args = _parse_args()
    challenge = _decode_hex(args.challenge)
    if challenge is None:
        challenge = DEFAULT_IAP2_CHALLENGE
        print(f"[auth] Using default {len(challenge)}-byte iAP2 challenge.", flush=True)
    try:
        auth, accessory, wifi = asyncio.run(
            run_client(args.mac, args.channel, challenge,
                       register_profile=not args.no_profile, adapter=args.adapter))
    except KeyboardInterrupt:
        print("[iap2] Interrupted by user; waiting for IVI reset handled.", flush=True)
        return

if __name__ == "__main__":
    main()
