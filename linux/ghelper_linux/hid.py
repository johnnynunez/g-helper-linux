"""Narrow ASUS P14 keyboard protocol; no commands during discovery.

Packet source: upstream 9173e953 app/USB/AsusHid.cs WriteInput pads
feature reports, app/USB/Aura.cs DirectBrightness uses 5A BA C5 C4 level,
and app/Input/InputDispatcher.cs HardwareFnLock uses 5A D0 4E (00=on).
Windows protocol provenance is not proof of Linux device-state validation.
"""
from pathlib import Path
import fcntl
import os
import re
import stat
import struct


# Exact descriptor read without opening hidraw3 on the P14. Unknown revisions
# remain disabled even if they advertise a superficially similar feature ID.
P14_DESCRIPTOR = bytes.fromhex(
    "05010906a1018502050709e009e109e209e309e409e509e609e715002501750195088102"
    "95017508810395057501050819012905910295017503910395067508150025e70507190029dd8100c0"
    "050c0901a101850319002a3c021500263c02751095018100c0"
    "0631ff0976a101855a19002aff00150026ff0075089505810019002aff00150026ff007508953fb100c0"
    "05010980a101850619002aff00150026ff00750895018100c0")

EXPERIMENTAL_REASON = (
    "Experimental upstream HID protocol; Linux physical command state is unverified. "
    "Transport acceptance != verified physical state; no state readback is known."
)


class HidError(RuntimeError):
    """Keyboard discovery or an explicit command failed safely."""


class HidKeyboard:
    def __init__(self, sys_device, device_path, transport=None):
        self.sys_device = Path(sys_device)
        self.device_path = Path(device_path)
        self.transport = transport if transport is not None else send_feature_report

    @property
    def writable(self):
        try:
            return bool(self.device_path.stat().st_mode & 0o222) and os.access(self.device_path, os.R_OK | os.W_OK)
        except OSError:
            return False

    def set_brightness(self, value: int) -> None:
        """Submit upstream 0–3 brightness; completion verifies transport only."""
        if type(value) is not int or not 0 <= value <= 3:
            raise ValueError("HID keyboard brightness must be an integer from 0 to 3")
        self._send(bytes((0x5A, 0xBA, 0xC5, 0xC4, value)))

    def set_fn_lock(self, enabled: bool) -> None:
        """Submit upstream Fn-lock; completion verifies transport, not state."""
        if type(enabled) is not bool:
            raise ValueError("Fn-lock requires a boolean")
        self._send(bytes((0x5A, 0xD0, 0x4E, 0x00 if enabled else 0x01)))

    def _send(self, command):
        self._validate()
        if not self.writable:
            raise HidError(f"Permission denied for keyboard {self.device_path}; no privileged helper is used")
        payload = command.ljust(64, b"\x00")

        try:
            written = self.transport(self.device_path, payload)
        except OSError as exc:
            raise HidError(f"Keyboard feature report failed: {exc}; physical state unknown") from exc
        if written != len(payload):
            raise HidError(f"Short HID feature report: accepted {written!r} of {len(payload)} bytes; state unknown")

    def _validate(self):
        if not re.fullmatch(r"0018:0B05:4B42\.[0-9A-Fa-f]+", self.sys_device.name):
            raise HidError("Unknown HID device; only I2C ASUS keyboard 0B05:4B42 is allowed, never EC 4543")
        try:
            descriptor = (self.sys_device / "report_descriptor").read_bytes()
            if descriptor != P14_DESCRIPTOR or feature_report_lengths(descriptor) != {0x5A: 64}:
                raise HidError("Unknown keyboard report descriptor; expected P14 feature report 5A length 64")
            if not re.fullmatch(r"hidraw\d+", self.device_path.name) or not (self.sys_device / "hidraw" / self.device_path.name).exists():
                raise HidError("Keyboard hidraw mapping unavailable or disconnected")
            if not self.device_path.exists():
                raise HidError("Keyboard hidraw node disconnected")
        except (OSError, ValueError) as exc:
            raise HidError(f"Cannot validate keyboard descriptor: {exc}") from exc


def send_feature_report(device_path, payload):
    """Linux hidraw ABI (asm-generic ioctl layout, aarch64/x86).

    Called only for an explicit command. A full return length confirms kernel
    transport acceptance, not the physical Fn-lock/backlight state.
    """
    brightness = len(payload) == 64 and payload[:4] == b"\x5a\xba\xc5\xc4" and payload[4] in range(4) and not any(payload[5:])
    fn_lock = len(payload) == 64 and payload[:3] == b"\x5a\xd0\x4e" and payload[3] in (0, 1) and not any(payload[4:])
    if not (brightness or fn_lock):
        raise ValueError("Only padded 64-byte upstream keyboard brightness/Fn-lock feature reports are allowed")
    fd = os.open(device_path, os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        if not stat.S_ISCHR(os.fstat(fd).st_mode):
            raise HidError("Keyboard hidraw target is not a character device")
        info = bytearray(struct.calcsize("=IHH"))
        fcntl.ioctl(fd, (2 << 30) | (len(info) << 16) | (ord("H") << 8) | 0x03, info, True)
        if struct.unpack("=IHH", info) != (0x18, 0x0B05, 0x4B42):
            raise HidError("Opened HID identity is not I2C ASUS keyboard 0B05:4B42; refusing report")
        size = bytearray(4)
        fcntl.ioctl(fd, (2 << 30) | (4 << 16) | (ord("H") << 8) | 0x01, size, True)
        if struct.unpack("=I", size)[0] != len(P14_DESCRIPTOR):
            raise HidError("Opened keyboard descriptor size differs from allowlisted P14")
        # struct hidraw_report_descriptor: uint32 size + uint8 value[4096].
        descriptor = bytearray(4100)
        descriptor[:4] = size
        fcntl.ioctl(fd, (2 << 30) | (4100 << 16) | (ord("H") << 8) | 0x02, descriptor, True)
        if descriptor[:4] != size or bytes(descriptor[4:4 + len(P14_DESCRIPTOR)]) != P14_DESCRIPTOR:
            raise HidError("Opened keyboard report descriptor is not allowlisted P14")
        request = (3 << 30) | (len(payload) << 16) | (ord("H") << 8) | 0x06
        return fcntl.ioctl(fd, request, bytearray(payload), True)
    finally:
        os.close(fd)


def discover_keyboard(sys_root=Path("/sys"), dev_root=Path("/dev"), transport=None):
    """Read descriptor/permissions only; do not open hidraw or issue ioctls."""
    reason = "No allowlisted I2C ASUS 0B05:4B42 keyboard/report descriptor found"
    found = []
    for device in sorted((Path(sys_root) / "bus/hid/devices").glob("0018:0B05:4B42.*")):
        nodes = sorted((device / "hidraw").glob("hidraw*"))
        if len(nodes) != 1:
            reason = "Keyboard hidraw mapping missing or ambiguous"
            continue
        keyboard = HidKeyboard(device, Path(dev_root) / nodes[0].name, transport)
        try:
            keyboard._validate()
        except HidError as exc:
            reason = str(exc)
            continue
        found.append(keyboard)
    if len(found) > 1:
        return None, "Multiple allowlisted keyboards; refusing ambiguous HID target"
    if not found:
        return None, reason
    keyboard = found[0]
    permission = "" if keyboard.writable else f" Permission denied for {keyboard.device_path}; no privileged helper is used."
    return keyboard, EXPERIMENTAL_REASON + permission


def feature_report_lengths(descriptor: bytes) -> dict[int, int]:
    """Return feature report lengths including the report-ID byte."""
    size = count = report_id = 0
    bits = {}
    stack = []
    offset = 0
    while offset < len(descriptor):
        prefix = descriptor[offset]
        offset += 1
        if prefix == 0xFE:
            raise ValueError("Unsupported HID long item")
        length = (0, 1, 2, 4)[prefix & 3]
        if offset + length > len(descriptor):
            raise ValueError("Truncated HID item")
        value = int.from_bytes(descriptor[offset:offset + length], "little")
        offset += length
        item_type, tag = (prefix >> 2) & 3, prefix >> 4
        if item_type == 1:
            if tag == 7:
                size = value
            elif tag == 8:
                if not 1 <= value <= 255:
                    raise ValueError("Invalid HID report ID")
                report_id = value
            elif tag == 9:
                count = value
            elif tag == 10:
                stack.append((size, count, report_id))
            elif tag == 11:
                if not stack:
                    raise ValueError("HID global stack underflow")
                size, count, report_id = stack.pop()
        elif item_type == 0 and tag == 11:
            if size <= 0 or count <= 0 or size * count > 65536:
                raise ValueError("Invalid or oversized HID feature report")
            bits[report_id] = bits.get(report_id, 0) + size * count
    if stack:
        raise ValueError("Unbalanced HID global stack")
    return {report_id: (length + 7) // 8 + 1 for report_id, length in bits.items()}
