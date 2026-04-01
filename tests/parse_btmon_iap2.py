"""Decode iAP2 link-layer frames embedded in btmon logs.

This utility scans a btmon text dump, extracts hexadecimal byte streams,
and walks them looking for iAP2 frames (0xFF5A header).  It mirrors the
pretty-print helpers from ``tests/dump_ivi_syn.py`` so we can understand
how the IVI handled each link-layer control flow without resetting it.

Usage:
    python -m tests.parse_btmon_iap2 --file btmon2.txt
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Iterable, Iterator, Tuple

try:  # Allow running as ``python -m tests.parse_btmon_iap2`` from repo root.
    from iap2.link_layer import (
        CONTROL_ACK,
        CONTROL_EAK,
        CONTROL_RST,
        CONTROL_SYN,
        LinkPacketHeader,
        LinkSynchronizationPayload,
        check_checksum,
    )
except ModuleNotFoundError:  # pragma: no cover
    import sys

    sys.path.append(str(Path(__file__).resolve().parents[1] / "example"))
    from iap2.link_layer import (  # type: ignore
        CONTROL_ACK,
        CONTROL_EAK,
        CONTROL_RST,
        CONTROL_SYN,
        LinkPacketHeader,
        LinkSynchronizationPayload,
        check_checksum,
    )


HEX_RE = re.compile(r"\b[0-9a-fA-F]{2}\b")
ACL_RE = re.compile(r"^[<>] ACL Data (TX|RX):")
IAP2_MARKER = b"\xFF\x55\x02\x00\xEE\x10"


def _extract_acl_payloads(path: Path) -> Iterator[Tuple[str, bytes]]:
    """Yield (direction, payload-bytes) tuples for each ACL Data block."""

    direction: str | None = None
    values: bytearray | None = None

    for raw_line in path.read_text().splitlines():
        line = raw_line.rstrip()
        if ACL_RE.match(line):
            if direction and values:
                yield direction, bytes(values)
            direction = "TX" if "TX" in line else "RX"
            values = bytearray()
            continue

        if direction is None or values is None:
            continue

        hex_field = line.lstrip()
        if "  " in hex_field:
            hex_field = hex_field.split("  ", 1)[0]
        tokens = HEX_RE.findall(hex_field)
        if tokens:
            for token in tokens:
                values.append(int(token, 16))
            continue

        if values and (
            not line.strip()
            or line[0] in "><=@"
            or line.startswith("blueman")
            or line.startswith("[")):
            yield direction, bytes(values)
            direction = None
            values = None

    if direction and values:
        yield direction, bytes(values)


def _iter_iap2_items(data: bytes) -> Iterator[Tuple[str, bytes, LinkPacketHeader | None, bytes]]:
    """Yield tuples describing frames (kind, raw, header, payload)."""

    i = 0
    limit = len(data)
    while i < limit:
        # Detect iAP2 support markers (FF 55 02 00 EE 10 [+ optional checksum]).
        if data.startswith(IAP2_MARKER, i):
            length = len(IAP2_MARKER)
            if i + length < limit and data[i + length] == 0x5A:
                length += 1
            raw = data[i : i + length]
            yield ("marker", raw, None, b"")
            i += length
            continue

        if i <= limit - 9 and data[i] == 0xFF and data[i + 1] == 0x5A:
            header_bytes = data[i : i + 9]
            header = LinkPacketHeader.from_bytes(header_bytes)
            if not header:
                i += 1
                continue

            trailer_len = header.length - 9
            frame_end = i + 9 + trailer_len
            if frame_end > limit:
                break

            payload = b""
            if header.length > 9:
                payload_with_checksum = data[i + 9 : frame_end]
                if not check_checksum(payload_with_checksum):
                    i += 1
                    continue
                payload = payload_with_checksum[:-1]

            raw = data[i:frame_end]
            yield ("frame", raw, header, payload)
            i = frame_end
            continue

        i += 1


def _describe_control(header: LinkPacketHeader) -> str:
    bits = []
    if header.control & CONTROL_RST:
        bits.append("RST")
    if header.control & CONTROL_SYN:
        bits.append("SYN")
    if header.control & CONTROL_ACK:
        bits.append("ACK")
    if header.control & CONTROL_EAK:
        bits.append("EAK")
    if not bits:
        bits.append(f"0x{header.control:02X}")
    return "+".join(bits)


def _print_lsp(payload: bytes) -> None:
    lsp = LinkSynchronizationPayload.from_bytes(payload)
    if not lsp:
        print("    <failed to parse LSP>")
        return
    sessions = ", ".join(
        f"(id=0x{s.id:02X}, type=0x{s.type:02X}, version=0x{s.version:02X})"
        for s in lsp.sessions
    )
    print(
        f"    LSP: max_outgoing=0x{lsp.max_outgoing:02X}, max_len=0x{lsp.max_len:04X}, "
        f"retrans_timeout=0x{lsp.retransmission_timeout:04X}, ack_timeout=0x{lsp.ack_timeout:04X}, "
        f"max_retrans=0x{lsp.max_retransmissions:02X}, max_ack=0x{lsp.max_ack:02X}, "
        f"sessions=[{sessions}]"
    )


def dump_frames(payloads: Iterable[Tuple[str, bytes]]) -> None:
    idx = 1
    for direction, data in payloads:
        for kind, raw, header, payload in _iter_iap2_items(data):
            if kind == "marker":
                print(
                    f"Frame 0x{idx:02X} ({direction}): MARKER ({raw.hex()})"
                )
                print()
                idx += 1
                continue

            if header is None:
                continue

            print(
                f"Frame 0x{idx:02X} ({direction}): control={_describe_control(header)}:0x{header.control:02X}, "
                f"seq=0x{header.seq:02X}, ack=0x{header.ack:02X}, "
                f"session=0x{header.session_id:02X}, payload_len=0x{len(payload):X}"
            )
            if header.control & CONTROL_SYN and payload:
                _print_lsp(payload)
            elif payload:
                preview = payload[:32].hex()
                suffix = "…" if len(payload) > 32 else ""
                print(f"    Payload: {preview}{suffix}")
            print()
            idx += 1


def main(argv: Iterable[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Parse iAP2 frames from btmon logs.")
    parser.add_argument("--file", default="btmon2.log", help="Path to btmon dump to parse.")
    args = parser.parse_args(list(argv) if argv is not None else None)

    path = Path(args.file)
    if not path.is_file():
        raise SystemExit(f"Could not read log file: {path}")

    dump_frames(_extract_acl_payloads(path))


if __name__ == "__main__":
    main()
