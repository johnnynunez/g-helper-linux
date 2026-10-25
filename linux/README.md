# Native Linux application

Status: experimental 0.1.0. Target: ASUS ProArt P14 H7407BA / RTX Spark N1X, Linux ARM64. Other Linux machines may expose the standard telemetry/controls, but are not hardware-validated. No Windows emulation is involved.

## Start

From the repository root:

```sh
./linux/bin/g-helper-linux
./linux/bin/g-helper-linux --json
./linux/bin/g-helper-linux --diagnose
./linux/bin/g-helper-linux --smoke-test
```

The source launcher uses `/usr/bin/python3`, following symlinks to find the checkout. Headless collection needs only the Python standard library and, for GPU readings, `nvidia-smi` from the installed driver. No packages are installed automatically. NVIDIA queries have a bounded timeout and unsupported fields remain null.

For the GUI on Debian/Ubuntu, install the distro packages if missing:

```sh
sudo apt install python3-gi gir1.2-gtk-4.0
```

An unrelated Python virtual environment may not contain `gi`; use the source launcher or `/usr/bin/python3`. The CLI remains usable without GTK.

Optional user-local menu integration, from the repository root (keep the checkout in place):

```sh
mkdir -p ~/.local/bin ~/.local/share/applications
ln -s "$PWD/linux/bin/g-helper-linux" ~/.local/bin/g-helper-linux
cp linux/data/io.github.johnnynunez.GHelperLinux.desktop ~/.local/share/applications/
```

`~/.local/bin` must be on your desktop session's PATH. A wheel can also be built with `uv build linux`; GTK is a native distro dependency and is deliberately not bundled.

## Available features and limits

| Feature | Implementation | P14 validation boundary |
| --- | --- | --- |
| Identity | DMI vendor/model + machine architecture | Read-only; no serials/keys |
| NVIDIA metrics | `nvidia-smi` CSV, missing data stays null | Read-only; GPU power may be unsupported |
| Battery | Standard power_supply sysfs | Read-only |
| Thermals and fan RPM | hwmon sysfs with kernel labels | ACPI zone temperature is not called a measured CPU core temperature |
| Performance profile | Aggregate `/sys/firmware/acpi/platform_profile` interface | Absent on the current P14 kernel; class-only handlers are not yet implemented |
| Charge limit | power_supply charge_control_end_threshold | Absent on the current P14 kernel |
| Keyboard brightness | LED sysfs or narrowly gated ASUS HID | No LED interface on this kernel; HID permission normally denied |
| Fn Lock | Narrowly gated ASUS HID keyboard | Packet tests only; live Linux behavior not yet verified |
| Manual fan control / power limits / undervolting | Not enabled | Requires additional protocol and fail-safe validation |

GUI controls are disabled when unavailable or not writable. The application never silently elevates privileges, writes during refresh, or changes a device to initialize it.

## Explicit controls

These commands are requests to change hardware state, not read-only diagnostics:

```sh
./linux/bin/g-helper-linux --profile balanced
./linux/bin/g-helper-linux --charge-limit 80
./linux/bin/g-helper-linux --keyboard-brightness 2
./linux/bin/g-helper-linux --fn-lock on
```

Only values supported by the detected interface are accepted. The sysfs value is read back after a write. HID packets do not provide verified state readback: transport acceptance must not be confused with proven physical behavior. The program reports that limitation. No P16 power limit or battery preset is assumed for the P14.

### HID permissions (optional, not installed automatically)

The optional `data/70-ghelper-asus-keyboard.rules` grants access only to the active local desktop session and only to I2C ASUS keyboard `0B05:4B42`. It grants no access to the EC `0B05:4543` and does not use world-writable device permissions. Review the rule and keyboard implementation before choosing to test it. Installing it requires an explicit administrator action:

```sh
sudo install -m 644 linux/data/70-ghelper-asus-keyboard.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules
sudo udevadm trigger --subsystem-match=hidraw
```

Do not run the entire GUI as root. An inactive/remote session may still lack device access. No permission rule or hardware write is applied by the app itself.

## Tests and package

```sh
PYTHONPATH=linux python3 -m unittest discover -s linux/tests -v
GHELPER_GUI_TEST=1 PYTHONPATH=linux /usr/bin/python3 -m unittest discover -s linux/tests -v
uv build linux
```

The GTK test opens a real native window with an explicitly labelled test backend; `--smoke-test` opens it with actual machine readings, never mocked telemetry. The automated suite cannot prove physical keyboard operation or Windows runtime behavior. Temporary fixtures honor TMPDIR.

The Windows changes are maintained separately on `feat/proart-p14-h7407ba` in this fork, so Linux additions do not need to be proposed to the Windows upstream. No upstream PR or Windows hardware validation is implied.

## License

GNU GPL v3, matching the preserved upstream LICENSE. ASUS HID keyboard packet origins are the upstream `app/USB/AsusHid.cs` and keyboard-control source; unsupported EC fan access is not used by this app.
