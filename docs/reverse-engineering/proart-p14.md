# ProArt P14 H7407BA: reverse-engineering findings

## Scope and method

Read-only investigation of this P14 on Linux `7.0.0-2022-nvidia-bos` / aarch64. Used installed REA 6.8.0, its bundled `reverse-engineer-anything` skill, GNU objdump and readable sysfs metadata. No driver loading, HID requests, EC transactions, ACPI method evaluation, permission changes or firmware writes were performed. Static evidence does not establish runtime success.

REA's built-in managed/module/objdump providers worked through the CLI. The broader doctor reports missing optional deep-analysis engines and MCP registrations; those are not prerequisites for these successful static operations. No unrelated agent configuration was changed.

## Observations

1. ACPI device `ASUS2018:00`, namespace `\\_SB_.ASUP`, is present (`status=15`). Its physical platform node has no bound driver. `/sys/bus/wmi/devices` is absent on this kernel. `modinfo asus_wmi` and `modinfo asus_nb_wmi` report no module. This is stronger evidence than merely not seeing an app control, but it does not prove what ACPI methods the firmware implements.
2. The keyboard `0018:0B05:4B42.0004` is bound to the `asus` HID driver. The EC `0018:0B05:4543.0001` is bound to `hid-generic`. Do not confuse absent ASUS WMI support with absent keyboard-driver support.
3. The shipped `hid-asus.ko.zst` includes alias `hid:b0018g*v00000B05p00004B42`; its decompressed ELF64/aarch64 module SHA-256 is `1877bcbd35e9cb1873cd4abfd6000d7f70a81c059bb419818cd623c8f75d5495`. REA evidence: `ev_19a1cced023d2b57fc3f11d7e8ed337abb6130b4d2594b970c46d25dce8549dc`.
4. The keyboard's captured report descriptor has feature report ID `0x5A`, length 64 including the ID, and no `0x5D` Aura feature report. The Linux app gates direct keyboard commands on that exact descriptor, not vendor/product names alone.
5. REA symbol/disassembly inspection locates `asus_kbd_register_leds.isra.0` at module-relative `0x2078`, `asus_sync_fn_lock` at `0x1ab0`, and `asus_kbd_set_report` at `0x158`. Fn-lock's static byte construction is consistent with the upstream `5A D0 4E <boolean>` packet family; it does not establish the user-visible on/off interpretation or observed device state. Evidence: `ev_dcaf717c9eb756f55d5df2b1bbb68dbd1b38fd7e7afb1b908ceb73aaf8b0ebf7`.
6. The LED registration path allocates listener state and frees it rather than calling an external registration function. Checked relocations, not just disassembler labels: `0x2284` is `R_AARCH64_CALL26 devm_kfree`. Disassembly evidence: `ev_1b12feed979e2be94936652b2b04b5fb749cbfe279f7c2a7c5a3c9c294556886`; relocation evidence: `ev_341420a073971810cceff873fdb70ef079c0cb4082a07b37b6d20a79557e7317`.
7. Upstream Linux v7.0 `drivers/hid/hid-asus.c:777–783` calls `asus_hid_register_listener`, freeing listener state on error. `include/linux/platform_data/x86/asus-wmi.h:190–221` substitutes an inline `-ENODEV` return when `CONFIG_ASUS_WMI` is not reachable. The current kernel config enables `CONFIG_HID_ASUS=m` and `CONFIG_ACPI_PLATFORM_PROFILE=m`, but contains no ASUS WMI configuration symbol.
8. REA successfully inspected the built Windows managed DLL: 239 P/Invoke declarations across 22 native module references. That includes Windows kernel, power, display and NVIDIA boundaries; compiling for ARM alone would not produce a Linux application. Evidence: `ev_4fb751ce28d4d4414da30f3f37f9111d57df077112ca12e6c23b0265d70dbad7` (build-local identity, not an OEM firmware analysis).

## Inference, not runtime proof

The observed shipped LED-registration path is consistent with the upstream ASUS WMI listener stub being compiled into this ARM64 kernel. That provides a specific kernel-level explanation for the missing keyboard LED sysfs interface even though the keyboard is already bound to `hid-asus`. A standalone ARM-compatible LED/listener integration is a plausible kernel improvement, not a tested patch delivered by this app. The vendor module has not been proved byte-identical to the upstream v7.0 source.

The unbound `ASUS2018` platform node is a separate unresolved ACPI control path. Do not assume the P16's power/fan methods are correct for P14, or that restoring keyboard LEDs would restore platform-profile/battery controls.

## Remaining evidence required

- DSDT/SSDT contents are not readable by this unprivileged session. An explicitly authorized, read-only copy of DSDT and the SSDTs is needed to map `ASUP` methods. Do not copy all ACPI tables: `MSDM` can contain a Windows product key. Firmware dumps should be reviewed before public sharing.
- OEM `AsusEC_ARM.dll` / control-service binaries from the target's Windows installation or official driver package would enable static inspection and, later, authorized tracing on Windows. None were supplied here; no OEM behavior has been invented.
- Actual keyboard report acceptance and state, firmware profile limits, charge thresholds, EC telemetry and any future fan fail-safe must be checked separately. No manual fan controls are enabled by this research.

## Reproduce the static module inspection

```sh
set -C  # refuse to overwrite existing analysis output
zstd -d -c /lib/modules/$(uname -r)/kernel/drivers/hid/hid-asus.ko.zst > "$HOME/hid-asus-analysis.ko"
rea inspect-kernel-module "$HOME/hid-asus-analysis.ko" --format json
rea inspect-with-objdump "$HOME/hid-asus-analysis.ko" symbols --format json
rea inspect-with-objdump "$HOME/hid-asus-analysis.ko" disassemble --symbol asus_kbd_register_leds.isra.0 --format json
rea inspect-with-objdump "$HOME/hid-asus-analysis.ko" relocations --format json
```

Use an absent output filename rather than overwriting an existing analysis artifact. Compression must be removed into a separate file; never edit or replace the installed module. Function names/offsets and hashes are specific to the observed build.

Source references:
- https://raw.githubusercontent.com/torvalds/linux/v7.0/drivers/hid/hid-asus.c
- https://raw.githubusercontent.com/torvalds/linux/v7.0/include/linux/platform_data/x86/asus-wmi.h
- https://github.com/seerge/g-helper/pull/6218
- https://github.com/seerge/g-helper/issues/6223
