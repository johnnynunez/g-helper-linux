"""Native G-Helper Linux entry point; JSON collection never requires GTK."""
import argparse
import json
import sys


def main(argv=None, backend=None):
    parser = argparse.ArgumentParser(description='G-Helper Linux — experimental native ASUS controls')
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--json', action='store_true', help='print hardware JSON; read-only unless an explicit control is supplied')
    modes.add_argument('--diagnose', action='store_true', help='print a read-only, serial-free diagnostic snapshot')
    modes.add_argument('--gui', action='store_true', help='open the native GTK4 application (default)')
    modes.add_argument('--smoke-test', action='store_true', help='open GTK4, render a real snapshot and exit without writes')
    parser.add_argument('--version', action='version', version='G-Helper Linux 0.1.0 (experimental)')
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument('--profile', help='explicitly set a kernel-exposed platform profile')
    actions.add_argument('--charge-limit', type=int, help='explicitly set a kernel-exposed charge threshold')
    actions.add_argument('--keyboard-brightness', type=int, help='explicitly set keyboard brightness')
    actions.add_argument('--fn-lock', choices=['on', 'off'], help='explicitly set Fn Lock on an identified ASUS keyboard')
    args = parser.parse_args(argv)
    has_action = any(value is not None for value in [args.profile, args.charge_limit, args.keyboard_brightness, args.fn_lock])
    if has_action and (args.diagnose or args.gui or args.smoke_test):
        parser.error('write actions cannot be combined with GUI or read-only diagnostic/smoke modes')
    try:
        if backend is None:
            from .backend import LinuxBackend
            backend = LinuxBackend()
        action = False
        for value, method in [
            (args.profile, backend.set_platform_profile),
            (args.charge_limit, backend.set_charge_limit),
            (args.keyboard_brightness, backend.set_keyboard_brightness),
            (None if args.fn_lock is None else args.fn_lock == 'on', backend.set_fn_lock),
        ]:
            if value is not None:
                method(value)
                action = True
        if args.json or args.diagnose or action:
            print(json.dumps(backend.snapshot(), indent=2, ensure_ascii=False, allow_nan=False))
            return 0
        try:
            from .gui import run
        except ImportError as exc:
            raise RuntimeError('GTK4/PyGObject is unavailable in this interpreter. Try /usr/bin/python3 or use --json for headless operation.') from exc
        return run(backend, smoke_test=args.smoke_test)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f'G-Helper Linux: {exc}', file=sys.stderr)
        return 1
