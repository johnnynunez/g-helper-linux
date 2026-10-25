"""HID protocol tests use byte fixtures and an injected transport only."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ghelper_linux.hid import feature_report_lengths

# Read-only capture from 0018:0B05:4B42.0004/report_descriptor on the P14.
P14_DESCRIPTOR = bytes.fromhex(
    "05010906a1018502050709e009e109e209e309e409e509e609e715002501750195088102"
    "95017508810395057501050819012905910295017503910395067508150025e70507190029dd8100c0"
    "050c0901a101850319002a3c021500263c02751095018100c0"
    "0631ff0976a101855a19002aff00150026ff0075089505810019002aff00150026ff007508953fb100c0"
    "05010980a101850619002aff00150026ff00750895018100c0")


class HidTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR"))
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.sys = self.root / "sys"
        self.dev = self.root / "dev"
        self.dev.mkdir()

    def keyboard_fixture(self, identity="0018:0B05:4B42.0004", descriptor=P14_DESCRIPTOR, raw="hidraw3"):
        device = self.sys / "bus/hid/devices" / identity
        (device / "hidraw" / raw).mkdir(parents=True)
        (device / "report_descriptor").write_bytes(descriptor)
        node = self.dev / raw
        node.touch()
        return device, node

    def test_discovery_matches_exact_keyboard_descriptor_and_only_reads(self):
        from ghelper_linux.hid import discover_keyboard
        device, node = self.keyboard_fixture()
        self.keyboard_fixture("0018:0B05:4543.0001", raw="hidraw0")
        with patch("fcntl.ioctl", side_effect=AssertionError("discovery sent ioctl")), patch("os.open", side_effect=AssertionError("discovery opened device")):
            keyboard, reason = discover_keyboard(self.sys, self.dev)
        self.assertIsNotNone(keyboard)
        self.assertEqual(keyboard.device_path, node)
        self.assertEqual(keyboard.sys_device, device)
        self.assertIn("unverified", reason.lower())
        node.chmod(0)
        keyboard, reason = discover_keyboard(self.sys, self.dev)
        self.assertIsNotNone(keyboard)
        self.assertFalse(keyboard.writable)
        self.assertIn("permission", reason.lower())
        (device / "report_descriptor").write_bytes(P14_DESCRIPTOR[:-1])
        keyboard, reason = discover_keyboard(self.sys, self.dev)
        self.assertIsNone(keyboard)
        self.assertIn("descriptor", reason.lower())

    def test_discovery_rejects_ec_unknown_product_and_wrong_feature_length(self):
        from ghelper_linux.hid import discover_keyboard
        for identity, descriptor in (("0018:0B05:4543.0001", P14_DESCRIPTOR),
                                     ("0003:0B05:4B42.0004", P14_DESCRIPTOR),
                                     ("0018:0B05:4B42.0004", P14_DESCRIPTOR.replace(b"\x95\x3f", b"\x95\x3e"))):
            with self.subTest(identity=identity):
                device, _ = self.keyboard_fixture(identity, descriptor)
                self.assertIsNone(discover_keyboard(self.sys, self.dev)[0])
                import shutil
                shutil.rmtree(device)

    def test_feature_length_includes_report_id_not_input_report_count(self):
        self.assertEqual(feature_report_lengths(P14_DESCRIPTOR), {0x5A: 64})


    def test_descriptor_global_stack_accumulates_and_rejects_malformed_reports(self):
        # Report ID 5A: one byte, PUSH, one two-byte field, POP, one byte.
        descriptor = bytes.fromhex("855a75089501b100a47510b100b4b100")
        self.assertEqual(feature_report_lengths(descriptor), {0x5A: 5})
        for descriptor in (b"\x75", b"\xfe\x00\x00", b"\xb4", b"\x85\x00\xb1\x00", b"\xa4", b"\xb1\x00"):
            with self.subTest(descriptor=descriptor), self.assertRaises(ValueError):
                feature_report_lengths(descriptor)


    def test_brightness_sends_only_upstream_packet_with_descriptor_size_padding(self):
        from ghelper_linux.hid import discover_keyboard
        _, node = self.keyboard_fixture()
        sent = []
        def transport(path, payload):
            sent.append((path, payload))
            return len(payload)
        keyboard, _ = discover_keyboard(self.sys, self.dev, transport=transport)
        self.assertEqual(sent, [])
        self.assertIsNone(keyboard.set_brightness(2))
        self.assertEqual(sent, [(node, bytes.fromhex("5abac5c402") + bytes(59))])
        for value in (-1, 4, True, 1.1, "2"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                keyboard.set_brightness(value)
        self.assertEqual(len(sent), 1)


    def test_fn_lock_upstream_boolean_encoding_and_no_initialization(self):
        from ghelper_linux.hid import discover_keyboard
        _, node = self.keyboard_fixture()
        sent = []
        def transport(path, payload):
            sent.append((path, payload))
            return len(payload)
        keyboard, _ = discover_keyboard(self.sys, self.dev, transport=transport)
        keyboard.set_fn_lock(True)
        keyboard.set_fn_lock(False)
        self.assertEqual(sent, [(node, bytes.fromhex("5ad04e00") + bytes(60)),
                                (node, bytes.fromhex("5ad04e01") + bytes(60))])
        for value in (1, 0, None, "true"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                keyboard.set_fn_lock(value)
        self.assertEqual(len(sent), 2)


    def test_command_failures_and_revalidation_never_fall_back_to_ec(self):
        from ghelper_linux.hid import HidKeyboard, discover_keyboard
        device, node = self.keyboard_fixture()
        sent = []
        def broken(path, payload):
            sent.append(path)
            raise PermissionError("device denied")
        keyboard, _ = discover_keyboard(self.sys, self.dev, transport=broken)
        with self.assertRaisesRegex(RuntimeError, "denied"):
            keyboard.set_brightness(1)
        keyboard.transport = lambda path, payload: 63
        with self.assertRaisesRegex(RuntimeError, "Short"):
            keyboard.set_brightness(1)
        keyboard.transport = lambda path, payload: sent.append(path) or len(payload)
        (device / "report_descriptor").write_bytes(b"\x85\x5a")
        with self.assertRaisesRegex(RuntimeError, "descriptor"):
            keyboard.set_fn_lock(True)
        self.assertEqual(sent, [node])
        ec, ecnode = self.keyboard_fixture("0018:0B05:4543.0001", raw="hidraw0")
        rejected = HidKeyboard(ec, ecnode, transport=keyboard.transport)
        with self.assertRaisesRegex(RuntimeError, "Unknown HID"):
            rejected.set_brightness(1)
        self.assertEqual(sent, [node])
        (device / "report_descriptor").write_bytes(P14_DESCRIPTOR)
        node.unlink()
        with self.assertRaisesRegex(RuntimeError, "disconnected"):
            keyboard.set_fn_lock(False)


    def test_hidraw_transport_uses_sized_feature_ioctl_and_checks_opened_identity(self):
        from ghelper_linux.hid import send_feature_report
        import stat
        import struct
        from types import SimpleNamespace
        payload = bytes.fromhex("5abac5c402") + bytes(59)
        calls = []
        def ioctl(fd, request, data, mutate=True):
            calls.append((fd, request, bytes(data)))
            if request == 0x80084803:  # HIDIOCGRAWINFO, struct hidraw_devinfo: 8 bytes
                data[:] = struct.pack("=IHH", 0x18, 0x0B05, 0x4B42)
                return 0
            if request == 0x80044801:
                data[:] = struct.pack("=I", len(P14_DESCRIPTOR))
                return 0
            if request == (2 << 30) | (4100 << 16) | (ord("H") << 8) | 2:
                data[4:4 + len(P14_DESCRIPTOR)] = P14_DESCRIPTOR
                return 0
            self.assertEqual(request, 0xC0404806)  # HIDIOCSFEATURE(64)
            self.assertEqual(bytes(data), payload)
            return 64
        with patch("os.open", return_value=91) as opened, patch("os.close") as closed, patch("os.fstat", return_value=SimpleNamespace(st_mode=stat.S_IFCHR | 0o600)), patch("fcntl.ioctl", side_effect=ioctl):
            self.assertEqual(send_feature_report(Path("/not-live/hidraw3"), payload), 64)
        self.assertEqual(opened.call_args.args[1], os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW)
        self.assertEqual([request for _, request, _ in calls], [0x80084803, 0x80044801, (2 << 30) | (4100 << 16) | (ord("H") << 8) | 2, 0xC0404806])
        closed.assert_called_once_with(91)
        for vendor, product in ((0x0B05, 0x4543), (0x222A, 0x4B42)):
            calls.clear()
            def wrong_device(fd, request, data, mutate=True):
                calls.append(request)
                data[:] = struct.pack("=IHH", 0x18, vendor, product)
                return 0
            with patch("os.open", return_value=91), patch("os.close"), patch("os.fstat", return_value=SimpleNamespace(st_mode=stat.S_IFCHR | 0o600)), patch("fcntl.ioctl", side_effect=wrong_device):
                with self.assertRaisesRegex(RuntimeError, "identity"):
                    send_feature_report(Path("/not-live/hidraw3"), payload)
            self.assertEqual(calls, [0x80084803])


    def test_opened_hidraw_descriptor_must_match_before_feature_write(self):
        from ghelper_linux.hid import send_feature_report
        import stat
        import struct
        from types import SimpleNamespace
        payload = bytes.fromhex("5ad04e00") + bytes(60)
        size_request = 0x80044801
        descriptor_request = (2 << 30) | (4100 << 16) | (ord("H") << 8) | 2
        for descriptor in (P14_DESCRIPTOR, P14_DESCRIPTOR.replace(b"\x95\x3f", b"\x95\x3e")):
            calls = []
            def ioctl(fd, request, data, mutate=True):
                calls.append(request)
                if request == 0x80084803:
                    data[:] = struct.pack("=IHH", 0x18, 0x0B05, 0x4B42)
                elif request == size_request:
                    data[:] = struct.pack("=I", len(descriptor))
                elif request == descriptor_request:
                    self.assertEqual(len(data), 4100)
                    data[4:4 + len(descriptor)] = descriptor
                else:
                    self.assertIn(descriptor_request, calls, "feature sent before descriptor validation")
                    return 64
                return 0
            with self.subTest(valid=descriptor == P14_DESCRIPTOR), patch("os.open", return_value=91), patch("os.close"), patch("os.fstat", return_value=SimpleNamespace(st_mode=stat.S_IFCHR | 0o600)), patch("fcntl.ioctl", side_effect=ioctl):
                if descriptor == P14_DESCRIPTOR:
                    self.assertEqual(send_feature_report(Path("/not-live/hidraw3"), payload), 64)
                    self.assertEqual(calls, [0x80084803, size_request, descriptor_request, 0xC0404806])
                else:
                    with self.assertRaisesRegex(RuntimeError, "descriptor"):
                        send_feature_report(Path("/not-live/hidraw3"), payload)
                    self.assertNotIn(0xC0404806, calls)
        with patch("os.open", side_effect=AssertionError("invalid command opened device")):
            for malformed in (bytes(64), payload[:-1], bytes([0x5A, 0xBA, 0xC5, 0xC4, 4]) + bytes(59)):
                with self.subTest(malformed=malformed), self.assertRaises(ValueError):
                    send_feature_report(Path("/not-live/hidraw3"), malformed)


if __name__ == "__main__":
    unittest.main()
