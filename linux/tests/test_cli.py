"""Headless command contract; fixtures are not real telemetry."""
import contextlib
import io
import json
import unittest

from ghelper_linux.cli import main


class Backend:
    def __init__(self):
        self.calls = []

    def snapshot(self):
        self.calls.append(('snapshot',))
        return {'identity': {'model': 'test fixture'}, 'gpu': {'power_w': None},
                'battery': {}, 'sensors': [], 'capabilities': {}, 'warnings': []}

    def set_platform_profile(self, value):
        self.calls.append(('profile', value))

    def set_charge_limit(self, value):
        self.calls.append(('charge', value))

    def set_keyboard_brightness(self, value):
        self.calls.append(('keyboard', value))

    def set_fn_lock(self, value):
        self.calls.append(('fn', value))


class CliTests(unittest.TestCase):
    def invoke(self, args, backend=None):
        backend = backend or Backend()
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(args, backend=backend)
        return code, out.getvalue(), err.getvalue(), backend

    def test_json_collects_without_writing_and_preserves_missing_values(self):
        code, out, err, backend = self.invoke(['--json'])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)['gpu']['power_w'], None)
        self.assertEqual(backend.calls, [('snapshot',)])
        self.assertEqual(err, '')

    def test_explicit_controls_dispatch_only_the_requested_write(self):
        for args, expected in [
            (['--profile', 'quiet'], ('profile', 'quiet')),
            (['--charge-limit', '80'], ('charge', 80)),
            (['--keyboard-brightness', '2'], ('keyboard', 2)),
            (['--fn-lock', 'off'], ('fn', False)),
        ]:
            with self.subTest(args=args):
                code, out, err, backend = self.invoke(args + ['--json'])
                self.assertEqual(code, 0)
                self.assertEqual(backend.calls, [expected, ('snapshot',)])
                self.assertEqual(err, '')
                self.assertIn('identity', json.loads(out))

    def test_failed_control_is_reported_without_claiming_success(self):
        class Denied(Backend):
            def set_charge_limit(self, value):
                raise PermissionError('charge threshold is not writable')
        code, out, err, _ = self.invoke(['--charge-limit', '80', '--json'], Denied())
        self.assertEqual(code, 1)
        self.assertEqual(out, '')
        self.assertIn('not writable', err)

    def test_diagnostics_is_read_only(self):
        code, out, err, backend = self.invoke(['--diagnose'])
        self.assertEqual(code, 0)
        self.assertIn('identity', json.loads(out))
        self.assertEqual(backend.calls, [('snapshot',)])

    def test_default_and_smoke_modes_dispatch_native_gui(self):
        import types
        from unittest.mock import patch
        calls = []
        gui = types.SimpleNamespace(run=lambda backend, smoke_test=False: calls.append(smoke_test) or 0)
        with patch.dict('sys.modules', {'ghelper_linux.gui': gui}):
            self.assertEqual(self.invoke([])[0], 0)
            self.assertEqual(self.invoke(['--smoke-test'])[0], 0)
        self.assertEqual(calls, [False, True])

    def test_gui_and_diagnostics_never_accept_a_write_action(self):
        for mode in ['--diagnose', '--gui', '--smoke-test']:
            backend = Backend()
            with self.subTest(mode=mode), self.assertRaises(SystemExit) as error:
                self.invoke([mode, '--charge-limit', '80'], backend)
            self.assertEqual(error.exception.code, 2)
            self.assertEqual(backend.calls, [])


if __name__ == '__main__':
    unittest.main()
