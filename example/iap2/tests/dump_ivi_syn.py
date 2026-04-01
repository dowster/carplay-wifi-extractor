import sys
from pathlib import Path

if __package__ is None or __package__ == "":
    sys.path.append(str(Path(__file__).resolve().parents[2]))

from iap2.link_layer import (
    LinkPacketHeader,
    LinkSynchronizationPayload,
    CONTROL_SYN,
    LSPSession,
    gen_checksum,
)


HEX_DUMP = """
Device: ff 5a 00 09 40 07 2f 00 28 5a  
"""


def dump_ivi_syn():
    packets = list(_parse_packets())
    if not packets:
        raise SystemExit("HEX_DUMP is empty")

    for idx, (label, packet_bytes) in enumerate(packets, start=1):
        title = label or f"Packet #{idx}"
        print(f"=== {title} ===")
        dump_packet(packet_bytes)
        print()


def _parse_packets():
    for line in HEX_DUMP.strip().splitlines():
        cleaned = line.strip()
        if not cleaned:
            continue
        label = None
        if ":" in cleaned:
            label_part, hex_part = cleaned.split(":", 1)
            label = label_part.strip()
            cleaned = hex_part.strip()
        yield label, bytes.fromhex(cleaned)


def dump_packet(packet_bytes: bytes):
    if len(packet_bytes) < 11:
        raise SystemExit("Packet too short")

    header_bytes = packet_bytes[:9]

    header = LinkPacketHeader.from_bytes(header_bytes)
    if header is None:
        raise SystemExit("Header checksum invalid")

    payload_len = header.length - 10  # header (9) + checksum (1)
    if payload_len < 0 or payload_len + 10 > len(packet_bytes):
        raise SystemExit("Payload length mismatch")

    payload_bytes = packet_bytes[9:9 + payload_len]
    checksum = packet_bytes[9 + payload_len]

    computed_checksum = gen_checksum(payload_bytes)
    if computed_checksum != checksum:
        raise SystemExit(f"Payload checksum mismatch: "
                         f"expected 0x{checksum:02X}, "
                         f"got 0x{computed_checksum:02X}")

    lsp = LinkSynchronizationPayload.from_bytes(payload_bytes)
    if lsp is None:
        raise SystemExit("Payload parse failed")

    control_label = "SYN" if header.control == CONTROL_SYN else f"{header.control}"
    header_fmt = (
        f"Header(length={header.length}, control={control_label}, "
        f"seq={header.seq}, ack={header.ack}, session_id={header.session_id})"
    )
    payload_fmt = (
        "Payload("
        f"max_outgoing={lsp.max_outgoing}, "
        f"max_len={lsp.max_len}, "
        f"retransmission_timeout={lsp.retransmission_timeout}, "
        f"ack_timeout={lsp.ack_timeout}, "
        f"max_retransmissions={lsp.max_retransmissions}, "
        f"max_ack={lsp.max_ack}, "
        f"sessions={list(lsp.sessions)}"
        ")"
    )
    print(f"{header_fmt} [{payload_fmt}]")


if __name__ == "__main__":
    dump_ivi_syn()
