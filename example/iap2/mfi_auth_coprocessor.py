import hashlib
import os
import time
from struct import Struct

try:
    import smbus2  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    smbus2 = None

Word = Struct(">H")
DEV_ADDR = 0x10
_EMULATED_CERT = (
    b"-----BEGIN CERTIFICATE-----\n"
    b"Emulated MFi certificate payload\n"
    b"-----END CERTIFICATE-----"
)
_USE_EMULATED = os.environ.get("IAP2_EMULATE_MFI", "1") != "0"
_I2C_BUS = os.environ.get("IAP2_MFI_I2C_BUS", "/dev/i2c-11")
_bus = None
_hardware_error = None

if not _USE_EMULATED:
    if smbus2 is None:
        _hardware_error = "smbus2 is not installed"
    else:
        try:
            _bus = smbus2.SMBus(_I2C_BUS)
        except OSError as exc:
            _hardware_error = f"cannot open {_I2C_BUS}: {exc}"


def _read_i2c(addr, n):
    addr_msg = smbus2.i2c_msg.write(DEV_ADDR, bytes([addr]))
    read_msg = smbus2.i2c_msg.read(DEV_ADDR, n)
    for _ in range(5):
        try:
            _bus.i2c_rdwr(addr_msg, read_msg)
            return bytes(read_msg)
        except OSError:
            time.sleep(0.0005)
    raise Exception("timeout")


def _write_i2c(addr, arr):
    _bus.write_i2c_block_data(DEV_ADDR, addr, [int(x) for x in arr])


def read_certificate():
    if _USE_EMULATED:
        return _EMULATED_CERT
    if _bus is None:
        raise RuntimeError(f"MFi hardware requested but unavailable: {_hardware_error}")
    size = Word.unpack(_read_i2c(0x30, 2))[0]  # Read Accessory Certificate Data Length
    return _read_i2c(0x31, size)  # Read Accessory Certificate Data


def generate_challenge_response(challenge):
    if _USE_EMULATED:
        # Deterministic pseudo-response derived from challenge.
        digest = hashlib.sha256(b"IAP2_EMULATED_MFI" + challenge).digest()
        return digest
    if _bus is None:
        raise RuntimeError(f"MFi hardware requested but unavailable: {_hardware_error}")
    _write_i2c(0x20, Word.pack(len(challenge)))  # Write Challenge Data Length
    _write_i2c(0x21, challenge)  # Write Challenge Data
    _bus.write_byte_data(DEV_ADDR, 0x10, 0x01)  # Write Authentication Control and Status = Start
    time.sleep(0.01)
    for _ in range(10):
        try:
            if _bus.read_byte_data(DEV_ADDR, 0x10) == 0x10:  # Read Authentication Control and Status == Success
                break
        except OSError:
            pass
        time.sleep(0.1)
    else:
        raise Exception("timeout")
    size = Word.unpack(_read_i2c(0x11, 2))[0]  # Read Challenge Response Data Length
    return _read_i2c(0x12, size)  # Read Challenge Response Data


if __name__ == "__main__":
    print("CERT", read_certificate().hex())
    print("CERT", generate_challenge_response(b"12211213131231231231").hex())
