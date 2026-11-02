# 0.3.3 release verification

The user confirmed the local result and authorized publication. Publish the exact locally installed package, not a rebuilt runtime.

- Native tests249 passed;24 opt-in environment/device/account cases ignored.
- Package/release Python tests19 passed; clippy all targets, format and diff checks passed.
- Signed package verified with the native updater: archive inventory, isolated extraction and exact CLI0.3.3 passed. Versions0.3.0/0.3.1/0.3.2 detect it as newer; same0.3.3 is current.
- The installed executable hash matches the packaged candidate. Earlier local verification confirmed unchanged settings, connected account and enabled taskbar after startup acknowledgement.
- PNG routing/bottom alignment checked at96/144/192/240DPI. CPU/RAM/GPU/VRAM/API are512px alpha PNGs; Codex asset remains unchanged. Small-percent taskbar render inspected at96DPI. Real mixed-monitor transitions remain unverified.
- ZIP SHA256 `18c0f323ecc9dce21c6eced7d97576d1dc04f680d6990a3a9d90809b7b73c402`;315 explicit package files. Source history and packaged text scan had no key/token/private-key matches.
- Fixed installer-v2 and its URL remain unchanged; publish only ZIP, v2 manifest and signature.
- Retain older public releases because historical fixed download paths remain referenced and complete legacy-client dependencies are unknown. Installer-v1/installer-v2 are separate channels; do not hide/delete them as old app versions.

Public Latest/download verification is performed after publication; this document records the pre-publication gate, not a claim that upload alone proves installation.
