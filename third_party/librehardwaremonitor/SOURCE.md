# LibreHardwareMonitor binary provenance

- Upstream: `https://github.com/LibreHardwareMonitor/LibreHardwareMonitor`
- Version: `v0.9.6`
- Upstream commit: `3d331e3370efb858411f19511373eff65a218701`
- Official release ZIP SHA-256:
  `086d9f1b5a99e643edc2cfaaac16051685b551e4c5ac0b32a57c58c0e529c001`
- License: Mozilla Public License 2.0

Only the library and its direct managed runtime dependencies were copied from the verified release
ZIP. The GUI executable, drivers, service installers, storage polling configuration, and updater are
not included or run. The application enables only CPU and GPU hardware in this library; system RAM
comes from psutil.

The unmodified upstream source archive is included at
`third_party/source/LibreHardwareMonitor-v0.9.6-source.zip` (SHA-256
`28100B87FCD7A9A213C58FABE1174E4F3064975B5AB11799224766993CA1FBFC`).

