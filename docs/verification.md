# Verification and handoff

## Scope

Initial experimental native Linux fork plus exact ProArt P14 H7407BA Windows model corrections. Not full G-Helper feature parity or verified Windows hardware support. See the design and reverse-engineering notes for safety boundaries.

## Executed verification

- Linux: `GHELPER_GUI_TEST=1 PYTHONPATH=linux /usr/bin/python3 -m unittest discover -s linux/tests -v` — 31 tests, all passed, no skips. The native-window test checks real GTK4 mapping, updates current-state readback while preserving an unapplied selection, and rejects all hardware writes. Installed PyGObject emits upstream deprecation warnings on this host.
- `linux/bin/g-helper-linux --smoke-test` — actual machine backend, window mapped and real snapshot rendered, exit 0, no hardware writes.
- `linux/bin/g-helper-linux --json` — actual ASUS ProArt P14 H7407BA / aarch64 / RTX Spark N1X readings; NVIDIA memory fields null with explicit N/A warnings. Current kernel lacks aggregate platform-profile and charge-limit interfaces; keyboard permissions deny HID writes.
- Python 3.10 grammar check: all 11 package/test Python files passed. Static security scan found no hardcoded-secret, eval, pickle-load or shell=True patterns. Whitespace check passed.
- `udevadm verify linux/data/70-ghelper-asus-keyboard.rules` — success. Rule not installed.
- Native GTK WidgetPaintable capture generated from actual machine readings and visually inspected; stored at `docs/screenshots/ghelper-linux-p14.png`. This proves widget rendering, not desktop/compositor focus state.
- Wheel/source archive built with `uv build`; confirmed HID module, license, and source-archive optional integration files are present. Rebuild after source changes; do not reuse an earlier incomplete development wheel.
- Windows: `dotnet run --project tests/ModelCapabilities/ModelCapabilities.csproj -c Release` — 14/14 model cases passed.
- Official Linux ARM64 .NET SDK 10.0.403 cross-built the actual Windows solution and Release win-x64 project with zero warnings/errors. Single-file framework-dependent publish succeeded using the repository's FolderProfile.
- Published-format Windows executable identified as PE32+ Windows GUI x86-64. Its package requires .NET Desktop Runtime 10 x64, even on ARM Windows. The binary has not been executed on Windows or hardware-validated.

## Independent review

An independent reviewer read both repositories, the Windows diff, Linux backend/HID/frontend, tests, packaging and permission rule. No blocking security or logic findings remained. Minor observations were addressed: narrow profile documentation to the implemented aggregate sysfs interface, and display refreshed current control state separately from pending widget selections. The latter has RED→GREEN coverage, including a real GTK test.

## Significant decisions

- Preserve upstream Windows UI and source; Linux is a separate native GTK4 application in the fork, not a rewrite of the Windows app.
- Do not infer P14 power limits, battery presets or fan commands from the P16. No manual fan, power-limit or undervolt controls are enabled.
- Direct keyboard control is experimental and descriptor/identity-gated; transport success is not verified physical state. No keyboard reports were sent in this session.
- No root services, global device access, installed udev rules, kernel-module changes, ACPI calls, firmware changes or secrets/product-key collection.
- Preserve local branches and artifacts. Publish to the user's requested fork, not an upstream PR implying Windows hardware validation.

## Publication

The fork is published at https://github.com/johnnynunez/g-helper-linux with default branch `linux-native`. Remote refs were read back and matched local commits. No upstream PR was opened.

- Linux implementation commit `a2f80c263cfa0d001cec24bda97d0842c1f99dbf`: GitHub Actions Linux native succeeded, including the GTK4/Xvfb test. https://github.com/johnnynunez/g-helper-linux/actions/runs/40246795535
- Windows commit `907045784a21fcf677903f608dee64c106c431e1`, branch `feat/proart-p14-h7407ba`: the original Windows build workflow also succeeded on a Windows runner. https://github.com/johnnynunez/g-helper-linux/actions/runs/40246806797
- The Linux wheel was installed into an isolated venv with system GTK dependencies; its console entry point, real JSON collection and mapped-window smoke test succeeded from outside the source checkout. One first-run GLib-GIO cleanup warning was not reproduced when the installed-wheel and source smoke tests were repeated; both repeated runs exited 0 with empty stderr.
- Windows zip and Linux wheel/source archive are preserved in each local checkout's ignored `artifacts/` directory. The Windows zip contains the single-file executable, config, runtime/validation notes and GPL license.

## Remaining hardware work

Windows runtime validation; an authorized DSDT/SSDT copy for the unbound ASUS2018 ACPI path; optional OEM ARM control-service binaries; explicit keyboard-state validation; and fail-safe testing before any future fan-control implementation. Class-only platform-profile handlers are not implemented and are not advertised.
