# ProArt P14 H7407BA: Windows and Linux

User authorization: implement both Windows support and a native Linux fork. The request to proceed supersedes further design-interview pauses. The Linux fork is johnnynunez/g-helper-linux, derived from seerge/g-helper at 9173e953.

## Intent and approach
Extend upstream-style Windows support without rewriting its WinForms UI; build an independent GTK4 native Linux application in linux/ in the fork. Alternative considered: replace the whole Windows UI with a cross-platform framework. Rejected because it substantially increases regression and upstream-integration risk. Python 3.10+, GTK4/PyGObject and standard-library Linux hardware access are implementation choices, not user-specified technologies.

## Observed hardware
DMI ASUS ProArt P14 H7407BA; Linux aarch64; NVIDIA RTX Spark N1X. HID EC 0B05:4543, keyboard 0B05:4B42. Current kernel has ACPI temperature/fan readings, but no standard platform profile or charge-control threshold interfaces. Device presence is not proof of working commands. Do not collect DMI serials, ACPI MSDM or other product keys.

## Goals
- Windows: narrowly scoped P14 model/capability corrections supported by source/hardware evidence, regression tests and a real cross-build. Preserve already-integrated P16/N1X improvements; no duplicated PR 6218 changes.
- Linux: working native GTK4 dashboard and a headless JSON/diagnostic CLI. Real NVIDIA, battery, thermal/fan readings with unavailable data represented as null, not zero.
- Linux controls: capability-gated standard sysfs profile, battery limit and keyboard brightness where exposed, plus narrowly identified ASUS HID keyboard operations only when the report descriptor and known upstream protocol support them. Explicit user operations only; never automatic writes on startup or refresh.
- Tests: stdlib unittest with injected filesystem/transport, real Linux read-only run, native GUI smoke run, independent safety review.
- Publish implementation branches to the explicitly requested fork; keep original Windows source and history. Windows hardware validation remains unverified until run under Windows.

## Safety and non-goals
Do not assume P16 95 W limits or offsets on P14. No undervolting, GPU power writes, raw ACPI execution, manual EC fan writes, flashing, root services or blanket device permissions. Do not copy battery 60/80 presets without evidence. Unsupported controls must state why. No claim of full feature parity or verified Windows hardware support. Fan EC mailbox may be documented but manual control is deferred until protocol validation and fail-safe recovery testing. Unknown devices must be rejected for HID writes. No synthetic telemetry in the real dashboard.

## Boundaries
Windows original checkout /home/johnny/Projects/g-helper on feat/proart-p14-h7407ba. Linux fork /home/johnny/Projects/g-helper-linux on linux-native. No agent may edit the other agent's owned files. Parent owns GUI/CLI, packaging, integration and publication. Hardware-backend agent owns linux/ghelper_linux/backend.py, hid.py and their tests; Windows agent owns Windows checkout only.
