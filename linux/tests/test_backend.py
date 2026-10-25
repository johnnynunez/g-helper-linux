"""Backend tests use scratch-rooted temporary sysfs trees, never live writes."""
import json
import os
from pathlib import Path
import platform
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from ghelper_linux.backend import LinuxBackend


class BackendTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR"))
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.sys = self.root / "sys"
        self.dev = self.root / "dev"
        self.sys.mkdir()
        self.dev.mkdir()
        self.backend = LinuxBackend(self.sys, self.dev, runner=self.no_gpu)

    @staticmethod
    def no_gpu(*args, **kwargs):
        raise FileNotFoundError("nvidia-smi")

    def put(self, relative, value):
        path = self.sys / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(value), encoding="utf-8")
        return path

    def test_empty_snapshot_is_json_safe_with_unknowns_not_zero(self):
        result = self.backend.snapshot()
        json.dumps(result, allow_nan=False)
        self.assertEqual(result["identity"], {
            "vendor": None, "model": None, "architecture": platform.machine()})
        self.assertTrue(all(value is None for value in result["gpu"].values()))
        self.assertTrue(all(value is None for value in result["battery"].values()))
        self.assertEqual(result["sensors"], [])
        for name in ("platform_profile", "charge_limit", "keyboard_brightness", "fn_lock"):
            cap = result["capabilities"][name]
            self.assertFalse(cap["available"])
            self.assertFalse(cap["writable"])
            self.assertIsNone(cap["value"])
            self.assertEqual(cap["choices"], [])
            self.assertTrue(cap["reason"])
        self.assertIsNone(result["capabilities"]["keyboard_brightness"]["max"])
        self.assertTrue(result["warnings"])


    def test_identity_hwmon_and_system_battery_are_read_without_writes(self):
        self.put("class/dmi/id/sys_vendor", "ASUS\n")
        self.put("class/dmi/id/product_name", "ProArt P14 H7407BA\n")
        self.put("class/power_supply/aaa-peripheral/type", "Battery")
        self.put("class/power_supply/aaa-peripheral/scope", "Device")
        self.put("class/power_supply/aaa-peripheral/capacity", "0")
        for key, value in dict(type="Battery", capacity=71, status="Discharging", power_now=12340000).items():
            self.put("class/power_supply/BAT0/" + key, value)
        self.put("class/hwmon/hwmon2/name", "acpitz_0")
        self.put("class/hwmon/hwmon2/temp1_input", "49000")
        self.put("class/hwmon/hwmon1/name", "acpi_fan")
        self.put("class/hwmon/hwmon1/fan1_input", "2570")
        self.put("class/hwmon/hwmon5/name", "nvme")
        self.put("class/hwmon/hwmon5/temp1_label", "Composite")
        self.put("class/hwmon/hwmon5/temp1_input", "41850")
        before = {str(p): p.read_bytes() for p in self.sys.rglob("*") if p.is_file()}
        with patch.object(Path, "write_text", side_effect=AssertionError("snapshot wrote")), patch("os.open", side_effect=AssertionError("snapshot opened HID")):
            result = self.backend.snapshot()
        self.assertEqual(result["identity"]["model"], "ProArt P14 H7407BA")
        self.assertEqual(result["identity"]["vendor"], "ASUS")
        self.assertEqual(result["battery"], dict(capacity_percent=71, status="Discharging", power_w=12.34, charge_limit_percent=None))
        self.assertIn(dict(label="acpitz_0 temp1", kind="temperature", value=49.0, unit="°C"), result["sensors"])
        self.assertIn(dict(label="acpi_fan fan1", kind="fan", value=2570, unit="RPM"), result["sensors"])
        self.assertIn(dict(label="nvme Composite", kind="temperature", value=41.85, unit="°C"), result["sensors"])
        self.assertEqual(before, {str(p): p.read_bytes() for p in self.sys.rglob("*") if p.is_file()})


    def test_partial_malformed_and_disconnected_sensors_stay_unknown(self):
        self.put("class/hwmon/hwmon0/name", "acpitz_0")
        self.put("class/hwmon/hwmon0/temp1_input", "not-a-number")
        self.put("class/hwmon/hwmon0/fan1_input", "-1")
        self.put("class/power_supply/BAT0/type", "Battery")
        self.put("class/power_supply/BAT0/capacity", "101")
        self.put("class/power_supply/BAT0/current_now", "-2000000")
        self.put("class/power_supply/BAT0/voltage_now", "12000000")
        result = self.backend.snapshot()
        self.assertIsNone(result["battery"]["capacity_percent"])
        self.assertEqual(result["battery"]["power_w"], 24.0)
        self.assertTrue(all(sensor["value"] is None for sensor in result["sensors"]))
        self.assertTrue(any("Malformed" in warning for warning in result["warnings"]))
        self.assertTrue(any("range" in warning for warning in result["warnings"]))
        real_read = Path.read_text
        def disappearing(path, *args, **kwargs):
            if path.name.endswith("_input"):
                raise OSError("device disconnected")
            return real_read(path, *args, **kwargs)
        with patch.object(Path, "read_text", disappearing):
            result = self.backend.snapshot()
        self.assertTrue(all(sensor["value"] is None for sensor in result["sensors"]))
        self.assertTrue(any("disconnected" in warning for warning in result["warnings"]))


    def test_nvidia_csv_uses_explicit_readonly_query_and_timeout(self):
        calls = []
        def runner(argv, **kwargs):
            calls.append((argv, kwargs))
            return subprocess.CompletedProcess(argv, 0, '"NVIDIA RTX Spark N1X", 54, 17, 23.45, 345, 65536\n', '')
        result = LinuxBackend(self.sys, self.dev, runner=runner).snapshot()
        self.assertEqual(result["gpu"], dict(name="NVIDIA RTX Spark N1X", temperature_c=54.0,
            utilization_percent=17.0, power_w=23.45, memory_used_mib=345.0, memory_total_mib=65536.0))
        argv, kwargs = calls[0]
        self.assertEqual(argv, ["nvidia-smi", "--query-gpu=name,temperature.gpu,utilization.gpu,power.draw,memory.used,memory.total", "--format=csv,noheader,nounits"])
        self.assertEqual(kwargs, dict(capture_output=True, text=True, timeout=3, check=False))


    def test_nvidia_partial_unsupported_malformed_failures_are_not_fabricated(self):
        for text in ('N1X, N/A, [Not Supported], 0, nan, inf\n', 'N1X, -1, 101, -2, broken, N/A\n'):
            with self.subTest(text=text):
                def runner(argv, **kwargs):
                    return subprocess.CompletedProcess(argv, 0, text, '')
                result = LinuxBackend(self.sys, self.dev, runner=runner).snapshot()
                self.assertEqual(result["gpu"]["name"], "N1X")
                for key in ("temperature_c", "utilization_percent", "memory_used_mib", "memory_total_mib"):
                    self.assertIsNone(result["gpu"][key])
                self.assertTrue(result["warnings"])
                json.dumps(result, allow_nan=False)
        for output, returncode in (('', 0), ('bad,csv', 0), ('N1X, 1, 1, 1, 1, 1', 1)):
            with self.subTest(output=output):
                def runner(argv, **kwargs):
                    return subprocess.CompletedProcess(argv, returncode, output, 'driver unavailable')
                result = LinuxBackend(self.sys, self.dev, runner=runner).snapshot()
                self.assertTrue(all(v is None for v in result["gpu"].values()))
                self.assertTrue(result["warnings"])
        for error in (subprocess.TimeoutExpired('nvidia-smi', 3), PermissionError('denied')):
            with self.subTest(error=error):
                def runner(*args, **kwargs):
                    raise error
                result = LinuxBackend(self.sys, self.dev, runner=runner).snapshot()
                self.assertTrue(all(v is None for v in result["gpu"].values()))
                self.assertTrue(result["warnings"])


    def test_platform_profile_choices_permissions_and_verified_explicit_write(self):
        path = self.put("firmware/acpi/platform_profile", "balanced\n")
        self.put("firmware/acpi/platform_profile_choices", "quiet balanced performance\n")
        cap = self.backend.snapshot()["capabilities"]["platform_profile"]
        self.assertTrue(cap["available"])
        self.assertTrue(cap["writable"])
        self.assertEqual(cap["choices"], ["quiet", "balanced", "performance"])
        self.assertEqual(cap["value"], "balanced")
        self.assertIsNone(self.backend.set_platform_profile("quiet"))
        self.assertEqual(path.read_text().strip(), "quiet")
        for value in ("turbo", "quiet\nperformance", 2, ""):
            with self.subTest(value=value), self.assertRaises((ValueError, RuntimeError)):
                self.backend.set_platform_profile(value)
        path.chmod(0o444)
        self.assertFalse(self.backend.snapshot()["capabilities"]["platform_profile"]["writable"])
        with self.assertRaisesRegex(RuntimeError, "[Pp]ermission|writable"):
            self.backend.set_platform_profile("balanced")
        path.unlink()
        with self.assertRaisesRegex(RuntimeError, "unavailable|exposed"):
            self.backend.set_platform_profile("balanced")


    def test_charge_limit_is_system_battery_only_with_percent_validation(self):
        self.put("class/power_supply/BAT0/type", "Battery")
        path = self.put("class/power_supply/BAT0/charge_control_end_threshold", "100")
        cap = self.backend.snapshot()["capabilities"]["charge_limit"]
        self.assertTrue(cap["available"])
        self.assertTrue(cap["writable"])
        self.assertEqual(cap["value"], 100)
        self.assertEqual(cap["choices"], list(range(1, 101)))
        self.backend.set_charge_limit(82)
        self.assertEqual(path.read_text().strip(), "82")
        self.assertEqual(self.backend.snapshot()["battery"]["charge_limit_percent"], 82)
        for value in (0, -1, 101, True, 80.5, "80"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.backend.set_charge_limit(value)
        path.unlink()
        with self.assertRaisesRegex(RuntimeError, "unavailable|exposed"):
            self.backend.set_charge_limit(80)
        self.put("class/power_supply/BAT0/scope", "Device")
        self.put("class/power_supply/BAT0/charge_control_end_threshold", "100")
        self.assertFalse(self.backend.snapshot()["capabilities"]["charge_limit"]["available"])


    def test_keyboard_led_limits_validation_and_readback(self):
        unrelated = self.put("class/leds/input0::capslock/brightness", 0)
        self.put("class/leds/input0::capslock/max_brightness", 1)
        path = self.put("class/leds/asus::kbd_backlight/brightness", 1)
        maximum = self.put("class/leds/asus::kbd_backlight/max_brightness", 3)
        cap = self.backend.snapshot()["capabilities"]["keyboard_brightness"]
        self.assertTrue(cap["available"])
        self.assertTrue(cap["writable"])
        self.assertEqual((cap["value"], cap["max"], cap["choices"]), (1, 3, [0, 1, 2, 3]))
        self.backend.set_keyboard_brightness(2)
        self.assertEqual(path.read_text().strip(), "2")
        self.assertEqual(unrelated.read_text(), "0")
        for value in (-1, 4, True, 1.1, "1"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.backend.set_keyboard_brightness(value)
        maximum.write_text("broken")
        self.assertFalse(self.backend.snapshot()["capabilities"]["keyboard_brightness"]["writable"])
        with self.assertRaises(RuntimeError):
            self.backend.set_keyboard_brightness(1)


    def test_hid_keyboard_capabilities_are_readonly_unknown_and_explicit_commands_only(self):
        from ghelper_linux.hid import P14_DESCRIPTOR, discover_keyboard
        device = self.sys / "bus/hid/devices/0018:0B05:4B42.0004"
        (device / "hidraw/hidraw3").mkdir(parents=True)
        (device / "report_descriptor").write_bytes(P14_DESCRIPTOR)
        node = self.dev / "hidraw3"
        node.touch()
        node.chmod(0o600)
        with patch("fcntl.ioctl", side_effect=AssertionError("snapshot ioctl")):
            result = self.backend.snapshot()
        for name in ("keyboard_brightness", "fn_lock"):
            cap = result["capabilities"][name]
            self.assertTrue(cap["available"])
            self.assertTrue(cap["writable"])
            self.assertIsNone(cap["value"])
            self.assertIn("unverified", cap["reason"])
        self.assertEqual(result["capabilities"]["keyboard_brightness"]["max"], 3)
        self.assertTrue(any("Transport acceptance" in text for text in result["warnings"]))
        sent = []
        def transport(path, payload):
            sent.append(payload)
            return len(payload)
        def discovery(sys_root, dev_root):
            return discover_keyboard(sys_root, dev_root, transport=transport)
        with patch("ghelper_linux.backend.discover_keyboard", side_effect=discovery):
            self.backend.set_keyboard_brightness(3)
            self.backend.set_fn_lock(True)
            self.assertIsNone(self.backend.snapshot()["capabilities"]["fn_lock"]["value"])
        self.assertEqual(sent, [bytes.fromhex("5abac5c403") + bytes(59), bytes.fromhex("5ad04e00") + bytes(60)])
        node.chmod(0)
        self.assertFalse(self.backend.snapshot()["capabilities"]["fn_lock"]["writable"])
        with self.assertRaisesRegex(RuntimeError, "[Pp]ermission"):
            self.backend.set_fn_lock(False)

    def test_all_four_setters_fail_clearly_without_interfaces(self):
        for method, value in (("set_platform_profile", "quiet"), ("set_charge_limit", 80),
                              ("set_keyboard_brightness", 1), ("set_fn_lock", True)):
            with self.subTest(method=method), self.assertRaisesRegex(RuntimeError, "unavailable|exposed|No allowlisted"):
                getattr(self.backend, method)(value)
        with self.assertRaises(ValueError):
            self.backend.set_fn_lock(1)


    def test_numeric_overflow_empty_values_and_unreadable_identity_are_safe(self):
        self.put("class/hwmon/hwmon0/temp1_input", "9" * 400)
        self.put("class/power_supply/BAT0/type", "Battery")
        self.put("class/power_supply/BAT0/current_now", "1" + "0" * 207)
        self.put("class/power_supply/BAT0/voltage_now", "1" + "0" * 207)
        self.put("class/dmi/id/product_name", "\n")
        result = self.backend.snapshot()
        self.assertIsNone(result["identity"]["model"])
        self.assertIsNone(result["sensors"][0]["value"])
        self.assertIsNone(result["battery"]["power_w"])
        json.dumps(result, allow_nan=False)
        self.assertTrue(any("Malformed" in warning for warning in result["warnings"]))
        real_read = Path.read_text
        def denied(path, *args, **kwargs):
            if path.name == "product_name":
                raise PermissionError("access denied")
            return real_read(path, *args, **kwargs)
        with patch.object(Path, "read_text", denied):
            result = self.backend.snapshot()
        self.assertIsNone(result["identity"]["model"])
        self.assertTrue(any("access denied" in warning for warning in result["warnings"]))


    def test_sysfs_write_errors_and_mismatches_never_report_success(self):
        path = self.put("firmware/acpi/platform_profile", "balanced")
        self.put("firmware/acpi/platform_profile_choices", "quiet balanced")
        with patch("os.write", return_value=1):
            with self.assertRaisesRegex(RuntimeError, "Short write"):
                self.backend.set_platform_profile("quiet")
        path.write_text("balanced")
        real_read = Path.read_text
        reads = []
        def unchanged(target, *args, **kwargs):
            if target == path:
                reads.append(target)
                return "balanced"
            return real_read(target, *args, **kwargs)
        with patch.object(Path, "read_text", unchanged):
            with self.assertRaisesRegex(RuntimeError, "verification failed"):
                self.backend.set_platform_profile("quiet")
        self.assertGreaterEqual(len(reads), 2)
        with patch("os.open", side_effect=PermissionError("kernel rejected")):
            with self.assertRaisesRegex(RuntimeError, "kernel rejected"):
                self.backend.set_platform_profile("quiet")
        (self.sys / "firmware/acpi/platform_profile_choices").unlink()
        with self.assertRaisesRegex(RuntimeError, "choices unavailable"):
            self.backend.set_platform_profile("quiet")


if __name__ == "__main__":
    unittest.main()
