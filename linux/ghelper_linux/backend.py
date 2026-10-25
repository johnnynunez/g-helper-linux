"""Read-only by default, standard-library Linux hardware access.

``LinuxBackend(sys_root=Path('/sys'), dev_root=Path('/dev'), runner=None)``
collects JSON-safe snapshots without opening hidraw or changing hardware.
An injected runner follows ``subprocess.run``'s signature. GPU telemetry is the
first NVIDIA device, with an explicit warning if multiple devices are present.
Battery power is an unsigned magnitude in watts (``status`` gives direction).
Temperature labels retain hwmon names: ACPI zones are not claimed as CPU cores.

The four ``set_*`` methods are explicit operations, returning None or raising
ValueError for invalid values / RuntimeError for failed or unavailable controls.
Sysfs writes require readback equality. HID commands verify transport length
only; their values remain None, including after a successful submission.
HID capabilities add ``source='hid'`` and ``verification`` to the common keys
and include an experimental-state warning. No initialization reports are sent.
"""
from pathlib import Path
import csv
import io
import math
import os
import platform
import subprocess
from typing import Any

from .hid import discover_keyboard


def _capability(reason) -> dict[str, Any]:
    return dict(available=False, writable=False, value=None, choices=[], reason=reason)


class ControlError(RuntimeError):
    """An explicit hardware operation was unavailable, rejected or unverifiable."""


class LinuxBackend:
    def __init__(self, sys_root=Path("/sys"), dev_root=Path("/dev"), runner=None):
        self.sys_root = Path(sys_root)
        self.dev_root = Path(dev_root)
        self.runner = runner if runner is not None else subprocess.run
        self._warnings = []

    def _read(self, path):
        try:
            return path.read_text(encoding="utf-8").strip() or None
        except (OSError, UnicodeError) as exc:
            if not isinstance(exc, FileNotFoundError):
                self._warnings.append(f"Cannot read {path}: {exc}")
            return None

    def _number(self, path, scale=1, minimum=None, maximum=None):
        text = self._read(path)
        if text is None:
            return None
        try:
            value = int(text) / scale if scale != 1 else int(text)
            if (minimum is not None and value < minimum) or (maximum is not None and value > maximum):
                self._warnings.append(f"Out-of-range reading {path}: {text!r}")
                return None
            return value
        except (ValueError, OverflowError):
            self._warnings.append(f"Malformed numeric reading {path}: {text!r}")
            return None

    def _battery_path(self):
        batteries = [p for p in sorted((self.sys_root / "class/power_supply").glob("*"))
                     if self._read(p / "type") == "Battery" and self._read(p / "scope") != "Device"]
        return next((p for p in batteries if p.name == "BAT0"), next(iter(batteries), None))

    def _battery(self):
        path = self._battery_path()
        result: dict[str, Any] = dict.fromkeys(("capacity_percent", "status", "power_w", "charge_limit_percent"))
        if path is not None:
            power = self._number(path / "power_now", 1_000_000, minimum=0)
            if power is None:
                current = self._number(path / "current_now", 1_000_000)
                voltage = self._number(path / "voltage_now", 1_000_000, minimum=0)
                if current is not None and voltage is not None:
                    power = abs(current * voltage)
                    if not math.isfinite(power):
                        self._warnings.append("Malformed battery power calculation: non-finite current × voltage")
                        power = None
            result.update(capacity_percent=self._number(path / "capacity", minimum=0, maximum=100),
                          status=self._read(path / "status"),
                          power_w=power,
                          charge_limit_percent=self._number(path / "charge_control_end_threshold", minimum=0, maximum=100))
        return result

    def _sensors(self):
        sensors = []
        for hwmon in sorted((self.sys_root / "class/hwmon").glob("hwmon*")):
            name = self._read(hwmon / "name") or hwmon.name
            for pattern, kind, unit, scale in (("temp*_input", "temperature", "°C", 1000),
                                               ("fan*_input", "fan", "RPM", 1)):
                for path in sorted(hwmon.glob(pattern)):
                    stem = path.name.removesuffix("_input")
                    label = self._read(hwmon / (stem + "_label")) or stem
                    sensors.append(dict(label=f"{name} {label}", kind=kind,
                                        value=self._number(path, scale, minimum=0 if kind == "fan" else None), unit=unit))
        return sensors

    def _gpu(self):
        keys = ("name", "temperature_c", "utilization_percent", "power_w", "memory_used_mib", "memory_total_mib")
        result: dict[str, Any] = dict.fromkeys(keys)
        argv = ["nvidia-smi", "--query-gpu=name,temperature.gpu,utilization.gpu,power.draw,memory.used,memory.total",
                "--format=csv,noheader,nounits"]
        try:
            process = self.runner(argv, capture_output=True, text=True, timeout=3, check=False)
        except (OSError, subprocess.SubprocessError) as exc:
            self._warnings.append(f"nvidia-smi unavailable: {exc}")
            return result
        if process.returncode:
            self._warnings.append(f"nvidia-smi failed ({process.returncode}): {process.stderr.strip()[:400]}")
            return result
        try:
            rows = list(csv.reader(io.StringIO(process.stdout), skipinitialspace=True, strict=True))
        except csv.Error as exc:
            self._warnings.append(f"Malformed nvidia-smi CSV: {exc}")
            return result
        if not rows or len(rows[0]) != len(keys):
            self._warnings.append("Malformed or empty nvidia-smi CSV; expected six fields")
            return result
        if len(rows) > 1:
            self._warnings.append("Multiple NVIDIA GPUs detected; showing first device only")
        for index, (key, text) in enumerate(zip(keys, rows[0])):
            text = text.strip()
            if not text or text.lower() in ("n/a", "[n/a]", "not supported", "[not supported]", "unknown"):
                self._warnings.append(f"NVIDIA {key} unavailable: {text or 'empty'}")
                continue
            if index == 0:
                result[key] = text
                continue
            try:
                value = float(text)
                if not math.isfinite(value) or value < 0 or (key == "utilization_percent" and value > 100):
                    raise ValueError("outside valid range")
                result[key] = value
            except ValueError:
                self._warnings.append(f"NVIDIA {key} malformed or unsupported: {text!r}")
        return result

    @staticmethod
    def _writable(path):
        try:
            return bool(path.stat().st_mode & 0o222) and os.access(path, os.W_OK)
        except OSError:
            return False

    def _profile_capability(self):
        path = self.sys_root / "firmware/acpi/platform_profile"
        cap = _capability("No standard platform_profile interface exposed; control unavailable")
        if not path.exists():
            return cap
        cap.update(available=True, value=self._read(path),
                   choices=(self._read(path.with_name("platform_profile_choices")) or "").split())
        cap["writable"] = self._writable(path) and cap["value"] is not None and bool(cap["choices"])
        cap["reason"] = "Standard sysfs interface; writes verified by readback" if cap["writable"] else "Interface not writable, unreadable or choices unavailable"
        return cap

    def _charge_capability(self):
        battery = self._battery_path()
        path = battery / "charge_control_end_threshold" if battery is not None else None
        cap = _capability("No standard battery charge threshold exposed; control unavailable")
        if path is None or not path.exists():
            return cap
        value = self._number(path, minimum=0, maximum=100)
        cap.update(available=True, value=value, choices=list(range(1, 101)),
                   writable=value is not None and self._writable(path))
        cap["reason"] = "Standard percent interface (1–100); firmware may reject unsupported values; writes verified by readback" if cap["writable"] else "Charge threshold unreadable or not writable"
        return cap

    def set_charge_limit(self, value: int) -> None:
        if type(value) is not int or not 1 <= value <= 100:
            raise ValueError("Charge limit must be an integer from 1 to 100 percent")
        cap = self._charge_capability()
        if not cap["writable"]:
            raise ControlError(cap["reason"])
        battery = self._battery_path()
        if battery is None:
            raise ControlError("System battery disconnected; charge limit unavailable")
        self._write_verified(battery / "charge_control_end_threshold", value)

    def _keyboard_led_path(self):
        return next(iter(sorted((self.sys_root / "class/leds").glob("*::kbd_backlight"))), None)

    def _keyboard_capability(self, hid_result=None):
        path = self._keyboard_led_path()
        cap = _capability("No supported keyboard brightness interface exposed; control unavailable")
        cap["max"] = None
        if path is None:
            return self._hid_capability("keyboard_brightness", hid_result)
        maximum = self._number(path / "max_brightness", minimum=1, maximum=65535)
        value = self._number(path / "brightness", minimum=0, maximum=maximum)
        cap.update(available=True, value=value, max=maximum,
                   choices=list(range(int(maximum) + 1)) if maximum is not None else [],
                   writable=maximum is not None and value is not None and self._writable(path / "brightness"))
        cap["reason"] = "Standard LED interface; writes verified by readback" if cap["writable"] else "Keyboard LED limits/value unreadable or brightness not writable"
        return cap

    def set_keyboard_brightness(self, value: int) -> None:
        if type(value) is not int or value < 0:
            raise ValueError("Keyboard brightness must be a non-negative integer")
        if self._keyboard_led_path() is None:
            keyboard, reason = discover_keyboard(self.sys_root, self.dev_root)
            if keyboard is None or not keyboard.writable:
                raise ControlError(reason)
            keyboard.set_brightness(value)
            return
        cap = self._keyboard_capability()
        if not cap["writable"]:
            raise ControlError(cap["reason"])
        if value > cap["max"]:
            raise ValueError(f"Keyboard brightness must be between 0 and {cap['max']}")
        path = self._keyboard_led_path()
        if path is None:
            raise ControlError("Keyboard LED disconnected")
        self._write_verified(path / "brightness", value)

    def _hid_capability(self, name, hid_result=None):
        keyboard, reason = hid_result if hid_result is not None else discover_keyboard(self.sys_root, self.dev_root)
        cap = _capability(reason)
        cap.update(available=keyboard is not None, writable=keyboard is not None and keyboard.writable)
        if keyboard is not None:
            cap.update(choices=[0, 1, 2, 3] if name == "keyboard_brightness" else [False, True],
                       source="hid", verification="transport-only; physical state unverified")
        if name == "keyboard_brightness":
            cap["max"] = 3 if keyboard is not None else None
        return cap

    def set_fn_lock(self, enabled: bool) -> None:
        if type(enabled) is not bool:
            raise ValueError("Fn-lock requires a boolean")
        keyboard, reason = discover_keyboard(self.sys_root, self.dev_root)
        if keyboard is None or not keyboard.writable:
            raise ControlError(reason)
        keyboard.set_fn_lock(enabled)

    def _write_verified(self, path, value):
        try:
            if not path.resolve().is_relative_to(self.sys_root.resolve()):
                raise ControlError("Refusing sysfs write outside configured sys_root")
            payload = f"{value}\n".encode("ascii")
            fd = os.open(path, os.O_WRONLY | os.O_TRUNC | os.O_CLOEXEC | os.O_NOFOLLOW)
            try:
                written = os.write(fd, payload)
                if written != len(payload):
                    raise ControlError(f"Short write to {path}; hardware state unknown")
            finally:
                os.close(fd)
            actual = path.read_text(encoding="utf-8").strip()
            if actual != str(value):
                raise ControlError(f"Write verification failed for {path}: requested {value!r}, read {actual!r}; state may have changed")
        except (OSError, UnicodeError) as exc:
            raise ControlError(f"Cannot write/verify {path}: {exc}; state may have changed") from exc

    def set_platform_profile(self, value: str) -> None:
        cap = self._profile_capability()
        if not cap["writable"]:
            raise ControlError(cap["reason"])
        if not isinstance(value, str) or value not in cap["choices"]:
            raise ValueError(f"Profile must be one of {cap['choices']}")
        self._write_verified(self.sys_root / "firmware/acpi/platform_profile", value)

    def snapshot(self) -> dict:
        """Collect current readings/capabilities. Never call any setter or ioctl."""
        self._warnings = []
        hid_result = discover_keyboard(self.sys_root, self.dev_root)
        if hid_result[0] is not None:
            self._warnings.append(hid_result[1])
        capabilities = {
            "platform_profile": self._profile_capability(),
            "charge_limit": self._charge_capability(),
            "keyboard_brightness": self._keyboard_capability(hid_result),
            "fn_lock": self._hid_capability("fn_lock", hid_result),
        }

        return {
            "identity": dict(vendor=self._read(self.sys_root / "class/dmi/id/sys_vendor"),
                             model=self._read(self.sys_root / "class/dmi/id/product_name"),
                             architecture=platform.machine()),
            "gpu": self._gpu(),
            "battery": self._battery(),
            "sensors": self._sensors(), "capabilities": capabilities,
            "warnings": self._warnings,
        }
